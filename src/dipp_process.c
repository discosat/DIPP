#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <fcntl.h>
#include <errno.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/ipc.h>
#include <sys/msg.h>
#include <sys/shm.h>
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
#include "quarantine.h"
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
#include "dipp_control.h"
#include "battery_simulator.h"
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
/* Set while a batch that failed once is being re-run: the executor then picks
   the lowest effort of every remaining stage regardless of the heuristic. */
int retry_lowest_effort = 0;

/* Durable record of every batch handed to the uplink, written after the
   upload and before the batch leaves its queue. The daemon's stdout is not
   durable under a power cut (ext4 drops its last seconds), so without this
   a batch that completed just before a reset is indistinguishable from one
   that was lost -- which is what the C3 tally must tell apart, and what the
   ground needs to reconcile downlinks after a reset. One line per batch,
   fsync'd; a few milliseconds on eMMC. */
#define COMPLETION_LEDGER "/usr/share/dipp/completed.log"
static void completion_ledger_append(const ImageBatch *b)
{
    int fd = open(COMPLETION_LEDGER, O_WRONLY | O_APPEND | O_CREAT, 0644);
    if (fd == -1)
    {
        printf("LEDGER_FAIL %s: %s\n", COMPLETION_LEDGER, strerror(errno));
        return;
    }
    char line[96];
    int n = snprintf(line, sizeof(line), "%s pipeline=%d num_images=%d\n", b->uuid, b->pipeline_id, b->num_images);
    if (write(fd, line, n) != n || fsync(fd) != 0)
        printf("LEDGER_FAIL %s: %s\n", COMPLETION_LEDGER, strerror(errno));
    close(fd);
}

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
            {
                struct timespec ts;
                clock_gettime(CLOCK_MONOTONIC, &ts);
                printf("Pipeline fully executed successfully uuid=%s ts_us=%lld deadline_us=%lld\n",
                       input_batch->uuid,
                       (long long)ts.tv_sec * 1000000 + ts.tv_nsec / 1000,
                       (long long)input_batch->priority);
            }

            if (image_batch_read_data(input_batch) != SUCCESS || input_batch->data == NULL)
            {
                printf("Error reading image data for batch %s\n", input_batch->uuid);
                MTR_END_FUNC();
                return BATCH_FAILED;
            }

            upload(input_batch->data, input_batch->num_images, input_batch->batch_size);
            completion_ledger_append(input_batch);

            /* Uploaded: the segment / file has done its job. Release it, do
               not merely detach -- see image_batch_release. In HYBRID mode
               the batch was presented to the modules as MEM; restore the
               mode so the ingest/checkpoint file goes too. */
            if (global_storage_mode == STORAGE_HYBRID)
                input_batch->storage_mode = STORAGE_HYBRID;
            image_batch_release(input_batch);
            quarantine_clear(input_batch);

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

/* Control messages already waiting when the daemon starts were sent to a
   previous daemon: the SysV queue outlives the process that created it. A
   stale CTL_BATTERY_ADVANCE would fast-forward THIS run's battery, so they
   are discarded, not acted on, and counted in the log. */
static void discard_stale_control_messages(void)
{
    int msg_queue_id = msgget(MSG_QUEUE_KEY, 0);
    if (msg_queue_id == -1)
        return;
    int n = 0;
    DippControlMsg m;
    while (msgrcv(msg_queue_id, &m, sizeof(m) - sizeof(long), DIPP_CTL_MTYPE, IPC_NOWAIT) != -1)
        n++;
    printf("CTL_STALE_DISCARDED n=%d\n", n);
}

/* Drain harness control messages (mtype DIPP_CTL_MTYPE) -- see dipp_control.h.
   Returns the number handled. */
static int drain_control_messages(void)
{
    int msg_queue_id = msgget(MSG_QUEUE_KEY, 0);
    if (msg_queue_id == -1)
        return 0;
    int handled = 0;
    for (;;)
    {
        DippControlMsg m;
        ssize_t r = msgrcv(msg_queue_id, &m, sizeof(m) - sizeof(long), DIPP_CTL_MTYPE, IPC_NOWAIT);
        if (r == -1)
            break;
        struct timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        long long ts_us = (long long)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
        switch (m.op)
        {
        case CTL_BATTERY_ADVANCE:
            battery_simulator_advance(m.arg_f);
            break;
        case CTL_BATTERY_SET_WH:
            battery_simulator_set_wh((float)m.arg_f);
            break;
        case CTL_MARK:
            printf("MARK ts_us=%lld scene=%d sim_t_s=%.2f phase=%.4f\n",
                   ts_us, m.arg_i, battery_simulator_time_s(), battery_simulator_phase());
            break;
        default:
            printf("CTL_UNKNOWN ts_us=%lld op=%d\n", ts_us, m.op);
            break;
        }
        handled++;
    }
    return handled;
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
        else if (strcmp(storage_mode_str, "HYBRID") == 0)
        {
            global_storage_mode = STORAGE_HYBRID;
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

/* HYBRID: forget a segment whose contents no longer match the batch's
   durable (file, progress) pair. */
static void hybrid_drop_segment(ImageBatch *b)
{
    if (b->shmid >= 0)
        shmctl(b->shmid, IPC_RMID, NULL);
    b->shmid = -1;
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

    retry_lowest_effort = quarantine_retry_pending(&batch);
    if (retry_lowest_effort)
        printf("RETRY uuid=%s progress=%d at lowest effort\n", batch.uuid, batch.progress);

    /* HYBRID: the queue entry names a file (the ingest frame at progress -1,
       or a checkpoint written when the scheduler last halted this batch) and
       a segment that may or may not still exist -- never after a reset. Load
       the file into a segment when needed, then run the modules on shared
       memory: they are handed STORAGE_MEM and know nothing of the file. */
    BatchOutcome outcome;
    char file_before[sizeof(batch.filename)];
    strncpy(file_before, batch.filename, sizeof(file_before));
    int progress_before = batch.progress;   /* the progress the durable file corresponds to */
    if (global_storage_mode == STORAGE_HYBRID)
    {
        if (!image_batch_shm_exists(&batch))
        {
            printf("HYBRID_LOAD uuid=%s progress=%d file=%s\n", batch.uuid, batch.progress, batch.filename);
            if (image_batch_load_to_shm(&batch) != SUCCESS)
            {
                /* The durable copy is unreadable: nothing can be done for
                   this batch. Count it as a failure so it is quarantined. */
                batch.storage_mode = STORAGE_HYBRID;
                outcome = BATCH_FAILED;
                goto dispose;
            }
        }
        batch.storage_mode = STORAGE_MEM;
    }
    outcome = process(&batch);
    if (global_storage_mode == STORAGE_HYBRID)
        batch.storage_mode = STORAGE_HYBRID;
dispose:
    retry_lowest_effort = 0;
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
        /* The scheduler halted this batch (no effort level met the energy or
           latency gate), so it will wait -- possibly for a long time -- and a
           reset during that wait would otherwise cost the work done so far.
           This is the one point where checkpointing an intermediate pays
           (analysis/stage5/README.md, persistence): write the segment to a
           file, fsync, then commit the queue entry naming it, then drop the
           previous file. A reset between write and commit leaves an orphan
           the startup scan reclaims and an entry still naming the old file. */
        if (global_storage_mode == STORAGE_HYBRID && batch.progress == progress_before)
        {
            /* Halted before any new module ran: the file already holds this
               state, nothing to checkpoint. */
        }
        else if (global_storage_mode == STORAGE_HYBRID)
        {
            if (image_batch_checkpoint(&batch) == SUCCESS)
                printf("CHECKPOINT uuid=%s progress=%d file=%s\n", batch.uuid, batch.progress, batch.filename);
            else
            {
                /* The durable pair is always (file, progress). Without a
                   checkpoint the entry keeps the old file AND the old
                   progress, and the segment -- which holds a later state --
                   is dropped so the next run reloads from the file. */
                printf("CHECKPOINT_FAIL uuid=%s progress=%d; entry keeps %s at progress %d\n",
                       batch.uuid, batch.progress, file_before, progress_before);
                hybrid_drop_segment(&batch);
                batch.progress = progress_before;
            }
        }
        if (src == partially_processed_pq)
        {
            int rc = pq_impl->commit_head(src, &batch);
            printf("COMMIT_HEAD queue=partial uuid=%s progress=%d rc=%d depth=%ld\n",
                   batch.uuid, batch.progress, rc,
                   (long)pq_impl->get_queue_size(src));
        }
        else
        {
            printf("ENQUEUE_PARTIAL uuid=%s progress=%d partial_depth_before=%ld\n",
                   batch.uuid, batch.progress,
                   (long)pq_impl->get_queue_size(partially_processed_pq));
            if (pq_impl->enqueue(partially_processed_pq, batch) != SUCCESS)
            {
                /* Partial queue full: leave the batch in ingest and retry later
                   rather than dropping it. */
                printf("Partial queue full; leaving batch %s in ingest\n", batch.uuid);
                break;
            }
            remove_from_queue(src, batch.uuid, "moved_to_partial");
        }
        if (global_storage_mode == STORAGE_HYBRID && strcmp(file_before, batch.filename) != 0)
            remove(file_before);           /* the checkpoint is committed; the old file is dead */
        break;
    case BATCH_FAILED:
    default:
    {
        /* First failure: leave the batch where it is (still the head of its
           queue, its data untouched) and let the next pass re-run it at the
           lowest effort. Second failure: quarantine -- record for downlink,
           release storage, remove. See quarantine.h. */
        int failures = quarantine_record_failure(&batch, err_last_value);
        if (failures <= QUARANTINE_MAX_RETRIES)
        {
            printf("Batch %s FAILED (%d); will retry once at lowest effort\n",
                   batch.uuid, failures);
            if (global_storage_mode == STORAGE_HYBRID)
            {
                /* The segment holds a state later than the file's; the entry
                   may only carry the file's progress, so the retry reloads
                   the file and redoes the earlier modules (bounded by one
                   batch's work). */
                hybrid_drop_segment(&batch);
                batch.progress = progress_before;
            }
            /* In MMAP mode progress advanced durably per module already; keep
               it so the retry resumes there rather than from scratch. */
            if (pq_impl->commit_head)
                pq_impl->commit_head(src, &batch);
        }
        else
        {
            quarantine_finalize(&batch);
            remove_from_queue(src, batch.uuid, "quarantined");
        }
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

    /* Report both queue depths at startup unconditionally. This is what
       distinguishes "the entry was never written durably" from "it was written
       and later lost" -- the orphan scan's own log is gated on finding
       something, so its absence proves nothing. */
    printf("STARTUP_QUEUES ingest=%ld partial=%ld\n",
           (long)pq_impl->get_queue_size(ingest_pq),
           (long)pq_impl->get_queue_size(partially_processed_pq));

    /* Both queues are mapped now, so anything on disk they do not reference is
       unreachable and can be reclaimed. */
    if (global_storage_mode == STORAGE_MMAP || global_storage_mode == STORAGE_HYBRID)
        reclaim_orphaned_batch_files();

    /* HYBRID: no segment survives a reset, and the kernel may hand a new
       segment the id an entry still carries -- so forget them all here.
       Every entry is reloaded from its file when it is next selected:
       ingest entries restart from the raw frame, halted entries resume from
       their checkpoint. */
    if (global_storage_mode == STORAGE_HYBRID)
    {
        PriorityQueue *qs[2] = {ingest_pq, partially_processed_pq};
        for (int q = 0; q < 2; q++)
        {
            size_t n = pq_impl->get_queue_size(qs[q]);
            for (size_t k = 0; k < n; k++)
                qs[q]->items[k].shmid = -1;
        }
    }

    discard_stale_control_messages();

    // Track last flush time to ensure mtr_flush is called at most once per 100ms
    struct timespec last_mtr_flush = {0, 0};

    while (1)
    {
        // drain the message queue (nowait)
        ImageBatch datarcv;
        while (get_message_from_queue(&datarcv, 0) == SUCCESS)
        {
            /* A frame arriving means the board is powered to take it. */
            battery_simulator_board_activity();
            struct timespec ts;
            clock_gettime(CLOCK_MONOTONIC, &ts);
            long long ts_us = (long long)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
            // push data onto the ingest priority queue
            if (pq_impl->enqueue(ingest_pq, datarcv) != SUCCESS)
            {
                /* Queue full. The failure used to be ignored, so the batch
                   vanished and its segment or file leaked. Under a burst
                   denser than the service rate this is the expected outcome
                   and a reported metric, so record it and release the data. */
                printf("INGEST_DROP ts_us=%lld uuid=%s pipeline=%d num_images=%d deadline_us=%lld "
                       "reason=queue_full ingest_depth=%ld\n",
                       ts_us, datarcv.uuid, datarcv.pipeline_id, datarcv.num_images,
                       (long long)datarcv.priority, (long)pq_impl->get_queue_size(ingest_pq));
                image_batch_release(&datarcv);
            }
            else
            {
                printf("INGEST ts_us=%lld uuid=%s pipeline=%d num_images=%d deadline_us=%lld slo_us=%lld "
                       "ingest_depth=%ld partial_depth=%ld\n",
                       ts_us, datarcv.uuid, datarcv.pipeline_id, datarcv.num_images,
                       (long long)datarcv.priority, (long long)(datarcv.priority - ts_us),
                       (long)pq_impl->get_queue_size(ingest_pq),
                       (long)pq_impl->get_queue_size(partially_processed_pq));
            }
            MTR_END(__FILE__, "enqueue_onto_ingest");
        }
        drain_control_messages();

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
