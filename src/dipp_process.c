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
#include "dipp_error.h"
#include "dipp_config.h"
#include "dipp_process.h"
#include "dipp_process_param.h"
#include "dipp_paramids.h"
#include "priority_queue.h"
#include "cost_store.h"
#include "vmem_storage.h"
#include "heuristics.h"
#include "process_module.h"
#include "pipeline_executor.h"

void process(ImageBatch *input_batch)
{
    int pipeline_result = load_pipeline_and_execute(input_batch);

    if (pipeline_result == FAILURE)
    {

        // TODO: Implement retry logic (allow a single retry)
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

            // TODO: Create factory for Image reader, with init, read, close, upload

            int fd = open(input_batch->filename, O_RDONLY, 0644);
            if (fd == -1)
            {
                set_error_param(MMAP_OPEN);
                return;
            }

            input_batch->data = mmap(NULL, input_batch->batch_size, PROT_READ, MAP_PRIVATE, fd, 0);
            if (input_batch->data == MAP_FAILED)
            {
                close(fd);
                set_error_param(MMAP_MAP);
                return;
            }

            close(fd);

            upload(input_batch->data, input_batch->num_images, input_batch->batch_size);

            if (munmap(input_batch->data, input_batch->batch_size) == -1)
            {
                set_error_param(MMAP_UNMAP);
                return;
            }

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
            enqueue(partially_processed_pq, *input_batch);

            printf("Batch pushed to partially processed queue\n");
        }
    }

    // Reset err values
    err_current_pipeline = 0;
    err_current_module = 0;
}

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

    return SUCCESS;
}

void process_images_loop()
{

    // TODO: Set storage mode.
    global_storage_mode = STORAGE_MMAP;

    ingest_pq = createPriorityQueue("/usr/share/dipp/queue_file");
    partially_processed_pq = createPriorityQueue("/usr/share/dipp/partially_processed_queue_file");

    cost_store_init(&cost_cache);

    // get the heuristic parameter
    switch (param_get_uint32(&heuristic))
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
        while (get_message_from_queue(&datarcv, do_wait) == SUCCESS)
        {
            // push data onto the ingest priority queue
            enqueue(ingest_pq, datarcv);
        }

        // pull from the partially_processed_pq first
        ImageBatch *batch = dequeue(partially_processed_pq);
        if (batch == NULL)
        {
            // if empty, pull from the ingest_pq
            batch = dequeue(ingest_pq);
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
        size_t queue_size = get_queue_size(partially_processed_pq);
        if (queue_size < MAX_PARTIAL_QUEUE_SIZE)
        {
            ImageBatch *new_batch = dequeue(ingest_pq);
            if (new_batch != NULL)
            {
                // process the batch (maybe partially)
                process(new_batch);
            }
        }
    }
}
