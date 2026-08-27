#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/ipc.h>
#include <sys/msg.h>
#include <sys/mman.h>
#include <param/param.h>
#include <csp/csp_types.h>
#include <param/param_client.h>
#include <glob.h>
#include <time.h>
#include "pipeline_executor.h"
#include "dipp_error.h"
#include "dipp_config.h"
#include "dipp_process.h"
#include "dipp_paramids.h"
#include "priority_queue.h"
#include "cost_store.h"
#include "vmem_storage.h"
#include "heuristics.h"
#include "process_module.h"
#include "image_store.h"
#include "image_batch.h"
#include "vmem_upload_local.h"
#include "utils/minitrace.h"
#include <limits.h>

PriorityQueue *ingest_pq = NULL;
PriorityQueue *partially_processed_pq = NULL;
PriorityQueueImpl *pq_impl = NULL;

CostStoreImpl *cost_store_impl = NULL;
CostStore *cost_store = NULL;

StorageMode global_storage_mode = STORAGE_MMAP;

Heuristic *current_heuristic = NULL;
/* The queue the batch currently being executed came from, so pipeline_executor
   can checkpoint progress into it after every module. Single-threaded: only
   run_one_batch sets it. */
PriorityQueue *current_batch_source = NULL;

// Process a single image batch, either fully or partially
// It executes the pipeline, either fully or partially, depending
// on the available resources. Based on this, it either uploads
// and cleans up the image batch, or pushes the image batch onto the
// partially processed queue.
BatchOutcome process(ImageBatch *input_batch)
{
    MTR_BEGIN_FUNC_S("batch_uuid", input_batch->uuid);
    printf("Processing batch with pipeline ID %d, progress %d\n", input_batch->pipeline_id, input_batch->progress);
    int pipeline_result = load_pipeline_and_execute(input_batch);
    printf("Pipeline execution returned %d\n", pipeline_result);
    printf("Current progress after execution: %d\n", input_batch->progress);

    if (pipeline_result == FAILURE)
    {
        // Something went wrong during the execution.
        // TODO (A8): quarantine with a retry counter instead of dropping.
        MTR_END_FUNC();
        return BATCH_FAILED;
    }
    else
    {
        int pipeline_length = get_pipeline_length(input_batch->pipeline_id);
        if (pipeline_length == FAILURE)
        {
            printf("Error getting pipeline length\n");
            MTR_END_FUNC();
            return BATCH_FAILED;
        }
        if (input_batch->progress == pipeline_length - 1)
        {
            printf("Pipeline fully executed successfully uuid=%s\n", input_batch->uuid);

            if (image_batch_read_data(input_batch) != SUCCESS || input_batch->data == NULL)
            {
                printf("Error reading image data for batch %s\n", input_batch->uuid);
                MTR_END_FUNC();
                return BATCH_FAILED;
            }

            upload(input_batch->data, input_batch->num_images, input_batch->batch_size);

            image_batch_cleanup(input_batch);

            // TODO: Uncomment the following lines to delete the files after processing

            // char filename_prefix[] = "/usr/share/dipp/data/batch_%s_*";
            // char glob_pattern[sizeof(filename_prefix) + 37];
            // snprintf(glob_pattern, sizeof(filename_prefix) + 37, filename_prefix, input_batch->uuid);

            // glob_t gstruct;
            // int r = glob(glob_pattern, GLOB_ERR, NULL, &gstruct);

            // if (r == 0)
            // {
            //     for (size_t i = 0; i < gstruct.gl_pathc; i++)
            //     {
            //         remove(gstruct.gl_pathv[i]);
            //     }
            // }
            // else
            // {
            //     printf("Error deleting files\n");
            // }

            err_current_pipeline = 0;
            err_current_module = 0;
            MTR_END_FUNC();
            return BATCH_COMPLETE;
        }
        else
        {
            printf("Pipeline partially executed successfully uuid=%s progress=%d\n", input_batch->uuid, input_batch->progress);
            err_current_pipeline = 0;
            err_current_module = 0;
            MTR_END_FUNC();
            return BATCH_PARTIAL;
        }
    }

    // Reset err values
    err_current_pipeline = 0;
    err_current_module = 0;
    MTR_END_FUNC();
    return BATCH_FAILED;
}

// Pull data from the message queue, additionally setting the storage
// attribute of the image batch
int get_message_from_queue(ImageBatch *datarcv, int do_wait)
{
    int msg_queue_id;
    if ((msg_queue_id = msgget(MSG_QUEUE_KEY, 0)) == -1)
    {
        /* Report the transition, not every poll. This is the normal state
           before any producer has run, and the loop spins at ~1 kHz, so
           reporting unconditionally emitted 600-900 lines per second (148,371
           in one short idle session) and rewrote the log_status param just as
           often. */
        static int msgq_absent_reported = 0;
        if (!msgq_absent_reported)
        {
            set_error_param(MSGQ_NOT_FOUND);
            msgq_absent_reported = 1;
        }
        return FAILURE;
    }


    struct
    {
        long mtype;
        char mtext[sizeof(ImageBatch)];
    } msg_buffer;

    ssize_t msg_size = msgrcv(msg_queue_id, &msg_buffer, sizeof(msg_buffer.mtext), 1, do_wait ? 0 : IPC_NOWAIT);
    if (msg_size == -1)
    {
        // set_error_param(MSGQ_EMPTY);
        return FAILURE;
    }

    MTR_BEGIN(__FILE__, "enqueue_onto_ingest");

    // Ensure that the received message size is not larger than the ImageBatch structure
    if (msg_size > sizeof(ImageBatch))
    {
        // set_error_param(MSGQ_EMPTY);
        printf("Received %ld bytes, expected %ld bytes\n", msg_size, sizeof(ImageBatch));
        MTR_END(__FILE__, "enqueue_onto_ingest");
        return FAILURE;
    }

    // Copy the data to the datarcv buffer
    memcpy(datarcv, &msg_buffer, msg_size);

    /* Persist the payload before accepting the batch. The return value used to
       be discarded, so a batch whose data failed to persist was enqueued anyway
       and then failed later at module 0 looking like a durability bug. Refusing
       it here keeps the queue's invariant honest: everything in it has its data
       on storage.

       Note the message has already been consumed by msgrcv at this point, so a
       rejected batch is gone. That window -- kernel queue to durable queue -- is
       the producer's to close, not DIPP's: see the scope note in
       analysis/stage3-c3/README.md. */
    if (image_batch_setup_storage(datarcv, global_storage_mode) != SUCCESS)
    {
        printf("INGEST_REJECT uuid=%s reason=persist_failed\n", datarcv->uuid);
        return FAILURE;
    }

    return SUCCESS;
}

// Retrieve the storage mode and heuristic from environment variables
// Defaults of MMAP and LOWEST_EFFORT are used if not set or invalid
void get_env_vars()
{
    const char *storage_mode_str = getenv("STORAGE_MODE");
    if (storage_mode_str != NULL)
    {
        if (strcmp(storage_mode_str, "MEM") == 0)
        {
            global_storage_mode = STORAGE_MEM;
        }
        else if (strcmp(storage_mode_str, "MMAP") == 0)
        {
            global_storage_mode = STORAGE_MMAP;
        }
        else
        {
            printf("Unknown STORAGE_MODE '%s', defaulting to MMAP\n", storage_mode_str);
            global_storage_mode = STORAGE_MMAP;
        }
    }

    const char *heuristic_str = getenv("HEURISTIC");
    if (heuristic_str != NULL)
    {
        if (strcmp(heuristic_str, "LOWEST_EFFORT") == 0)
        {
            current_heuristic = &lowest_effort_heuristic;
        }
        else if (strcmp(heuristic_str, "BEST_EFFORT") == 0)
        {
            current_heuristic = &best_effort_heuristic;
        }
        else
        {
            printf("Unknown HEURISTIC '%s', defaulting to BEST_EFFORT\n", heuristic_str);
            current_heuristic = &best_effort_heuristic;
        }
    }
}

void update_heuristic(int ingest_queue_depth, int partial_queue_depth)
{
    Heuristic *previous_heuristic = current_heuristic;

    MTR_COUNTER(__FILE__, "ingest_queue_depth", ingest_queue_depth);
    MTR_COUNTER(__FILE__, "partial_queue_depth", partial_queue_depth);

    int total_queue_depth = ingest_queue_depth + partial_queue_depth;
    if (total_queue_depth < LOW_QUEUE_DEPTH_THRESHOLD
        // && partial_queue_depth < PARTIAL_QUEUE_SIZE_THRESHOLD
        )
    {
        current_heuristic = &best_effort_heuristic;
    }
    else
    {
        // either the partially processed queue is almost full, or the total queue depth is high
        current_heuristic = &lowest_effort_heuristic;
    }

    // log in case of change
    if (previous_heuristic != current_heuristic)
    {
        if (current_heuristic == &best_effort_heuristic)
        {
            MTR_INSTANT_C(__FILE__, "update_heuristc", "heuristic", "BEST_EFFORT");
        }
        else
        {
            MTR_INSTANT_C(__FILE__, "update_heuristc", "heuristic", "LOWEST_EFFORT");
        }
    }
}



/* Remove persisted batch files that no queue entry references.
 *
 * Failed or abandoned batches used to leave their data files behind forever:
 * persist_data_if_necessary() IPC_RMIDs the shared-memory segment before the
 * enqueue, and the cleanup glob in process() is commented out, so nothing ever
 * reclaimed them. Measured on the board, one run left 412 files holding 2.6 GB
 * -- 46% of the 5.7 GB partition, enough to fill the device in about two runs.
 *
 * Runs once at startup, after the queues are mapped, so "referenced" means
 * referenced by a batch that survived the last shutdown. Anything else is
 * unreachable by definition: filenames are UUID-based and never regenerated.
 */
static void reclaim_orphaned_batch_files(void)
{
    glob_t g;
    if (glob("/usr/share/dipp/data/batch_*", 0, NULL, &g) != 0)
        return;

    size_t removed = 0, kept = 0;
    for (size_t i = 0; i < g.gl_pathc; i++)
    {
        int referenced = 0;
        PriorityQueue *queues[2] = {ingest_pq, partially_processed_pq};
        for (int q = 0; q < 2 && !referenced; q++)
        {
            if (!queues[q])
                continue;
            size_t n = pq_impl->get_queue_size(queues[q]);
            for (size_t k = 0; k < n; k++)
            {
                if (strcmp(queues[q]->items[k].filename, g.gl_pathv[i]) == 0)
                {
                    referenced = 1;
                    break;
                }
            }
        }
        if (referenced)
        {
            kept++;
        }
        else if (remove(g.gl_pathv[i]) == 0)
        {
            removed++;
            printf("ORPHAN_REMOVE %s\n", g.gl_pathv[i]);
        }
    }
    /* Log what the queues actually reference, so "kept 0 referenced" can be told
       apart from "the queues were empty". Getting that wrong would make this
       scan delete a file a queued batch still needs. */
    for (int q = 0; q < 2; q++)
    {
        PriorityQueue *qq = (q == 0) ? ingest_pq : partially_processed_pq;
        if (!qq)
            continue;
        size_t n = pq_impl->get_queue_size(qq);
        for (size_t k = 0; k < n; k++)
            printf("ORPHAN_SCAN_REF queue=%s uuid=%s progress=%d filename=%s\n",
                   (q == 0) ? "ingest" : "partial", qq->items[k].uuid,
                   qq->items[k].progress, qq->items[k].filename);
    }

    globfree(&g);
    if (removed || kept)
        printf("Orphan scan: removed %zu unreferenced batch files, kept %zu referenced\n",
               removed, kept);
}


/* Every removal from a durable queue goes through here, so a batch cannot leave
   one unlogged. Logs the batch we intended to remove alongside the one actually
   removed: dequeue() takes the heap root, and if that is ever a different batch
   than the one just processed, this is what shows it. */
static void remove_from_queue(PriorityQueue *src, const char *expect_uuid, const char *reason)
{
    ImageBatch *removed = pq_impl->dequeue(src);
    printf("QUEUE_REMOVE queue=%s reason=%s expect_uuid=%s removed_uuid=%s\n",
           (src == ingest_pq) ? "ingest" : "partial",
           reason, expect_uuid,
           removed ? removed->uuid : "NONE");
    if (removed)
    {
        if (strcmp(removed->uuid, expect_uuid) != 0)
            printf("QUEUE_MISMATCH expected=%s removed=%s\n", expect_uuid, removed->uuid);
        free(removed);
    }
}

/* Run at most one batch from `src`, using peek-process-commit.
 *
 * The batch stays in its durable queue for the whole time it is being
 * processed. Previously the loop dequeued first, so between the pop and the
 * upload the batch existed only in a malloc'd struct: a reset there lost it
 * outright, and with a 9 s high-effort pipeline against 3.6 s arrivals there was
 * almost always one batch in that state.
 *
 * Commit ordering is chosen so a crash duplicates work rather than losing it:
 *   - complete  -> upload, THEN remove from the queue. A reset in between
 *                  re-runs the batch, which is why image_batch_read_data must
 *                  fail loudly on a missing file rather than upload zeros.
 *   - partial   -> if it came from the partial queue, update it in place, which
 *                  avoids two entries for the same batch; if it came from
 *                  ingest, enqueue to partial FIRST and only then remove from
 *                  ingest. The reverse order has a window that loses the batch.
 *
 * Returns 1 if a batch was processed, 0 if the queue was empty.
 */
static int run_one_batch(PriorityQueue *src)
{
    ImageBatch batch;
    if (pq_impl->peek_copy(src, &batch) != 0)
        return 0;

    /* Idempotent upload. Completion marks the batch durably (progress set one
       past the last module) BEFORE it is removed from the queue, so a reset in
       that window leaves a batch that is still queued but already downlinked.
       Re-running it would double-downlink -- which is exactly what R1.2 asks
       about -- so recognise the mark and just drop it. */
    int pipeline_len = get_pipeline_length(batch.pipeline_id);
    if (pipeline_len != FAILURE && batch.progress >= pipeline_len)
    {
        printf("Batch %s was already uploaded before a reset; removing without re-upload\n",
               batch.uuid);
        remove_from_queue(src, batch.uuid, "already_uploaded");
        return 1;
    }

    printf("QUEUE_SELECT queue=%s uuid=%s progress=%d depth=%ld\n",
           (src == ingest_pq) ? "ingest" : "partial", batch.uuid, batch.progress,
           (long)pq_impl->get_queue_size(src));

    current_batch_source = src;

    setup_cache_if_needed();
    update_heuristic(pq_impl->get_queue_size(ingest_pq),
                     pq_impl->get_queue_size(partially_processed_pq));

    BatchOutcome outcome = process(&batch);
    current_batch_source = NULL;

    switch (outcome)
    {
    case BATCH_COMPLETE:
    {
        /* Mark as uploaded durably first, then remove. If the reset lands
           between these two, the batch is still queued but carries the mark and
           the check at the top of this function drops it without re-uploading.
           Doing the removal first instead would leave a window where a reset
           re-runs an already-downlinked batch. */
        if (pipeline_len != FAILURE)
        {
            batch.progress = pipeline_len;
            pq_impl->commit_head(src, &batch);
        }
        remove_from_queue(src, batch.uuid, "complete");
        break;
    }
    case BATCH_PARTIAL:
        if (src == partially_processed_pq)
        {
            pq_impl->commit_head(src, &batch);
        }
        else
        {
            if (pq_impl->enqueue(partially_processed_pq, batch) != SUCCESS)
            {
                /* Partial queue full: leave the batch in ingest and retry later
                   rather than dropping it. */
                printf("Partial queue full; leaving batch %s in ingest\n", batch.uuid);
                break;
            }
            remove_from_queue(src, batch.uuid, "moved_to_partial");
        }
        break;
    case BATCH_FAILED:
    default:
    {
        /* Drop it, as before, but say so. A8 replaces this with a quarantine
           queue carrying a retry counter and an error code. */
        printf("Batch %s FAILED; dropping (A8 will quarantine instead)\n", batch.uuid);
        remove_from_queue(src, batch.uuid, "failed");
        break;
    }
    }
    return 1;
}

void process_images_loop()
{
    MTR_BEGIN_FUNC();

    current_heuristic = &best_effort_heuristic;
    global_storage_mode = STORAGE_MMAP;

    get_env_vars();

    pq_impl = get_priority_queue_impl(global_storage_mode);

    pq_impl->init(&ingest_pq, "/usr/share/dipp/queue_file");
    pq_impl->init(&partially_processed_pq, "/usr/share/dipp/partially_processed_queue_file");

    cost_store_impl = get_cost_store_impl(global_storage_mode);
    cost_store_impl->init(&cost_store, CACHE_FILE);

    /* Both queues are mapped now, so anything on disk they do not reference is
       unreachable and can be reclaimed. */
    if (global_storage_mode == STORAGE_MMAP)
        reclaim_orphaned_batch_files();

    // Track last flush time to ensure mtr_flush is called at most once per 100ms
    struct timespec last_mtr_flush = {0, 0};

    while (1)
    {
        // drain the message queue (nowait)
        ImageBatch datarcv;
        while (get_message_from_queue(&datarcv, 0) == SUCCESS)
        {
            // push data onto the ingest priority queue
            pq_impl->enqueue(ingest_pq, datarcv);
            MTR_END(__FILE__, "enqueue_onto_ingest");
        }

        /* Flush the trace at most every 100 ms. Re-enabled: with this commented
           out the trace was written only by the signal handler, so any run that
           ended another way -- a crash, a kill, a power cut, which is precisely
           what C3 exercises -- produced no trace at all. */
        {
            struct timespec now;
            clock_gettime(CLOCK_MONOTONIC, &now);
            long elapsed_ms = (last_mtr_flush.tv_sec == 0)
                                  ? LONG_MAX
                                  : (now.tv_sec - last_mtr_flush.tv_sec) * 1000 + (now.tv_nsec - last_mtr_flush.tv_nsec) / 1000000;
            if (last_mtr_flush.tv_sec == 0 || elapsed_ms >= 100)
            {
                mtr_flush();
                last_mtr_flush = now;
            }
        }

        // Prefer the partially processed queue, then fall back to ingest.
        int did_work = run_one_batch(partially_processed_pq);
        if (!did_work)
            did_work = run_one_batch(ingest_pq);
        if (!did_work)
        {
            usleep(1000); // nothing queued; wait for new data
            continue;
        }

        // Keep ingest draining even while the partial queue has work, so a
        // steady stream of partially processed batches cannot starve arrivals.
        if (pq_impl->get_queue_size(partially_processed_pq) < MAX_PARTIAL_QUEUE_SIZE)
            run_one_batch(ingest_pq);
    }

    pq_impl->clean_up(ingest_pq);
    pq_impl->clean_up(partially_processed_pq);
    cost_store_impl->clean_up(cost_store);
    MTR_END_FUNC();
}
