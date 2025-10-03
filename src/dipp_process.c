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
#include "heuristics.h"

PriorityQueue *ingest_pq = NULL;
PriorityQueue *partially_processed_pq = NULL;
PriorityQueueImpl *pq_impl = NULL;

CostStoreImpl *cost_store_impl = NULL;
CostEntry *cost_cache = NULL;

StorageMode global_storage_mode = STORAGE_MMAP;

Heuristic *current_heuristic = NULL;

// Process a single image batch, either fully or partially
// It executes the pipeline, either fully or partially, depending
// on the available resources. Based on this, it either uploads
// and cleans up the image batch, or pushes the image batch onto the
// partially processed queue.
void process(ImageBatch *input_batch)
{
    int pipeline_result = load_pipeline_and_execute(input_batch);

    if (pipeline_result == FAILURE)
    {
        // Something went wrong during the execution.
        // TODO: Consider possible retries
        return;
    }
    else
    {
        int pipeline_length = get_pipeline_length(input_batch->pipeline_id);
        if (pipeline_length == FAILURE)
        {
            printf("Error getting pipeline length\n");
            return;
        }
        if (input_batch->progress == pipeline_length)
        {
            printf("Pipeline fully executed successfully\n");

            image_batch_read_data(input_batch);
            if (input_batch->data == NULL)
            {
                printf("Error reading image data\n");
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
                return;
            }

            printf("Batch pushed to partially processed queue\n");
        }
    }

    // Reset err values
    err_current_pipeline = 0;
    err_current_module = 0;
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
        set_error_param(MSGQ_EMPTY);
        return FAILURE;
    }

    // Ensure that the received message size is not larger than the ImageBatch structure
    if (msg_size > sizeof(ImageBatch))
    {
        set_error_param(MSGQ_EMPTY);
        printf("Received %ld bytes, expected %ld bytes\n", msg_size, sizeof(ImageBatch));
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
            printf("Unknown HEURISTIC '%s', defaulting to LOWEST_EFFORT\n", heuristic_str);
            current_heuristic = &lowest_effort_heuristic;
        }
    }
}

void process_images_loop()
{
    get_env_vars();

    pq_impl = get_priority_queue_impl(global_storage_mode);

    pq_impl->init(ingest_pq, "/usr/share/dipp/queue_file");
    pq_impl->init(partially_processed_pq, "/usr/share/dipp/partially_processed_queue_file");

    cost_store_impl = get_cost_store_impl(global_storage_mode);
    cost_store_impl->init(cost_cache);

    HEURISTIC_TYPE curr_heur = LOWEST_EFFORT;

    // get the heuristic parameter
    switch (curr_heur)
    {
    case LOWEST_EFFORT:
        current_heuristic = &lowest_effort_heuristic;
        break;
    case BEST_EFFORT:
        current_heuristic = &best_effort_heuristic;
        break;
    default:
        current_heuristic = &lowest_effort_heuristic;
        break;
    }

    while (1)
    {
        // drain the message queue (nowait)
        ImageBatch datarcv;
        while (get_message_from_queue(&datarcv, 0) == SUCCESS)
        {
            // push data onto the ingest priority queue
            pq_impl->enqueue(ingest_pq, datarcv);
        }

        // pull from the partially_processed_pq first
        ImageBatch *batch = pq_impl->dequeue(partially_processed_pq);
        if (batch == NULL)
        {
            // if empty, pull from the ingest_pq
            batch = pq_impl->dequeue(ingest_pq);
            if (batch == NULL)
            {
                // if empty, wait for new data
                continue;
            }
        }

        setup_cache_if_needed();

        // process the batch (maybe partially)
        process(batch);

        // if partial not full (let's say size<10), pull data from ingest_pq
        size_t queue_size = pq_impl->get_queue_size(partially_processed_pq);
        if (queue_size < MAX_PARTIAL_QUEUE_SIZE)
        {
            ImageBatch *new_batch = pq_impl->dequeue(ingest_pq);
            if (new_batch != NULL)
            {
                // process the batch (maybe partially)
                process(new_batch);
            }
        }
    }
}
