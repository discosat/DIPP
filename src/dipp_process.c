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

PriorityQueue *ingest_pq = NULL;
PriorityQueue *partially_processed_pq = NULL;
PriorityQueueImpl *pq_impl = NULL;

CostStoreImpl *cost_store_impl = NULL;
CostStore *cost_store = NULL;

StorageMode global_storage_mode = STORAGE_MMAP;

Heuristic *current_heuristic = NULL;

// Process a single image batch, either fully or partially
// It executes the pipeline, either fully or partially, depending
// on the available resources. Based on this, it either uploads
// and cleans up the image batch, or pushes the image batch onto the
// partially processed queue.
void process(ImageBatch *input_batch)
{
    MTR_BEGIN_FUNC_S("batch_uuid", input_batch->uuid);
    printf("Processing batch with pipeline ID %d, progress %d\n", input_batch->pipeline_id, input_batch->progress);
    int pipeline_result = load_pipeline_and_execute(input_batch);
    printf("Pipeline execution returned %d\n", pipeline_result);
    printf("Current progress after execution: %d\n", input_batch->progress);

    if (pipeline_result == FAILURE)
    {
        // Something went wrong during the execution.
        // TODO: Consider possible retries
        MTR_END_FUNC();
        return;
    }
    else
    {
        int pipeline_length = get_pipeline_length(input_batch->pipeline_id);
        if (pipeline_length == FAILURE)
        {
            printf("Error getting pipeline length\n");
            MTR_END_FUNC();
            return;
        }
        if (input_batch->progress == pipeline_length - 1)
        {
            printf("Pipeline fully executed successfully\n");

            image_batch_read_data(input_batch);
            if (input_batch->data == NULL)
            {
                printf("Error reading image data\n");
                MTR_END_FUNC();
                return;
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
        }
        else
        {
            printf("Pipeline partially executed successfully\n");

            // push the batch to the partial queue
            if (pq_impl->enqueue(partially_processed_pq, *input_batch) != SUCCESS)
            {
                printf("Error: Failed to enqueue batch to partially processed queue\n");
                MTR_END_FUNC();
                return;
            }

            printf("Batch pushed to partially processed queue\n");
        }
    }

    // Reset err values
    err_current_pipeline = 0;
    err_current_module = 0;
    MTR_END_FUNC();
}

// Pull data from the message queue, additionally setting the storage
// attribute of the image batch
int get_message_from_queue(ImageBatch *datarcv, int do_wait)
{
    int msg_queue_id;
    if ((msg_queue_id = msgget(MSG_QUEUE_KEY, 0)) == -1)
    {
        set_error_param(MSGQ_NOT_FOUND);
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

    // set storage attribute on the image batch
    image_batch_setup_storage(datarcv, global_storage_mode);

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

        // // Only flush tracing if at least 100ms elapsed since last flush
        // {
        //     struct timespec now;
        //     clock_gettime(CLOCK_MONOTONIC, &now);
        //     long elapsed_ms = (last_mtr_flush.tv_sec == 0)
        //                           ? LONG_MAX
        //                           : (now.tv_sec - last_mtr_flush.tv_sec) * 1000 + (now.tv_nsec - last_mtr_flush.tv_nsec) / 1000000;
        //     if (last_mtr_flush.tv_sec == 0 || elapsed_ms >= 100)
        //     {
        //         mtr_flush();
        //         last_mtr_flush = now;
        //     }
        // }

        // pull from the partially_processed_pq first
        ImageBatch *batch = pq_impl->dequeue(partially_processed_pq);
        if (batch == NULL)
        {
            // if empty, pull from the ingest_pq
            batch = pq_impl->dequeue(ingest_pq);
            if (batch == NULL)
            {
                // if empty, wait for new data
                usleep(1000); // sleep for 1ms before continuing
                continue;
            }
        }

        setup_cache_if_needed();

        update_heuristic(pq_impl->get_queue_size(ingest_pq), pq_impl->get_queue_size(partially_processed_pq));

        // process the batch (maybe partially)
        process(batch);

        if (batch != NULL)
        {
            free(batch);
        }

        // // // if partial not full (size<10 by default), pull data from ingest_pq
        ImageBatch *new_batch = NULL;
        size_t queue_size = pq_impl->get_queue_size(partially_processed_pq);
        if (queue_size < MAX_PARTIAL_QUEUE_SIZE)
        {
            new_batch = pq_impl->dequeue(ingest_pq);
            if (new_batch == NULL)
            {
                usleep(1000); // sleep for 1ms before continuing
                continue;
            }

            setup_cache_if_needed();

            update_heuristic(pq_impl->get_queue_size(ingest_pq), pq_impl->get_queue_size(partially_processed_pq));

            // process the batch (maybe partially)
            process(new_batch);

            if (new_batch != NULL)
            {
                free(new_batch);
            }
        }
    }

    pq_impl->clean_up(ingest_pq);
    pq_impl->clean_up(partially_processed_pq);
    cost_store_impl->clean_up(cost_store);
    MTR_END_FUNC();
}
