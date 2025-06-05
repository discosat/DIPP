#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/wait.h>
#include <sys/types.h>
#include <sys/ipc.h>
#include <sys/msg.h>
#include <sys/mman.h>
#include <param/param.h>
#include <param/param_client.h>
#include <csp/csp_types.h>
#include "mock_energy_server.h"
#include <param/param_client.h>
#include "mock_energy_server.h"
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <glob.h>
#include <time.h>
#include "dipp_error.h"
#include "dipp_config.h"
#include "dipp_process.h"
#include "dipp_process_param.h"
#include "dipp_paramids.h"
#include "priority_queue.h"
#include "cost_store.h"
#include "murmur_hash.h"
#include "vmem_storage.h"
// #include "vmem_upload.h"
#include "vmem_upload_local.h"
#include "metadata.pb-c.h"
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"
#include "logger.h"
#include "heuristics.h"

static int output_pipe[2];     // Pipe for inter-process result communication
static int error_pipe[2];      // Pipe for inter-process error communication
static int is_interrupted = 0; // Flag to indicate if the process was interrupted

Logger *logger;
CostEntry *cost_cache;
Heuristic *current_heuristic;

// Signal handler for timeout
void timeout_handler(int signum)
{
    printf("Module timeout reached\n");
    uint16_t error_code = MODULE_EXIT_TIMEOUT;
    write(error_pipe[1], &error_code, sizeof(uint16_t));
    exit(EXIT_FAILURE); // Exit the child process with failure status
}

int execute_module_in_process(ProcessFunction func, ImageBatch *input, ModuleParameterList *config)
{
    // Create a new process
    pid_t pid = fork();

    if (pid == 0)
    {
        // Set up signal handler for timeout and starm alarm timer
        signal(SIGALRM, timeout_handler);
        alarm(param_get_uint32(&module_timeout));

        // Child process: Execute the module function
        ImageBatch result = func(input, config, error_pipe);
        alarm(0); // stop timeout alarm
        size_t data_size = sizeof(result);
        write(output_pipe[1], &result, data_size); // Write the result to the pipe
        exit(EXIT_SUCCESS);
    }
    else
    {
        // Parent process: Wait for the child process to finish
        int status;
        waitpid(pid, &status, 0);

        if (WIFEXITED(status))
        {
            // Child process exited normally (EXIT_FAILURE)
            if (WEXITSTATUS(status) != 0)
            {
                uint16_t module_error;
                size_t res = read(error_pipe[0], &module_error, sizeof(uint16_t));
                if (res == FAILURE)
                    set_error_param(PIPE_READ);
                else if (res == 0)
                    set_error_param(MODULE_EXIT_NORMAL);
                else if (module_error < 100)
                    set_error_param(MODULE_EXIT_CUSTOM + module_error);
                else
                    set_error_param(module_error);

                // invalidate cache, to be rebuilt in next pipeline invocation
                invalidate_cache();

                fprintf(stderr, "Child process exited with non-zero status\n");
                return FAILURE;
            }
        }
        else
        {
            // Child process did not exit normally (CRASH)
            set_error_param(MODULE_EXIT_CRASH);
            // invalidate cache
            invalidate_cache();
            fprintf(stderr, "Child process did not exit normally\n");
            return FAILURE;
        }

        return SUCCESS;
    }
}

int execute_pipeline(Pipeline *pipeline, ImageBatch *data)
{
    /* Initiate communication pipes */
    if (pipe(output_pipe) == -1 || pipe(error_pipe) == -1)
    {
        set_error_param(PIPE_CREATE);
        return FAILURE;
    }

    for (size_t i = data->progress + 1; i < pipeline->num_modules; ++i)
    {
        size_t module_param_id;
        uint32_t picked_hash;

        COST_MODEL_LOOKUP_RESULT lookup_result = current_heuristic->heuristic_function(&pipeline->modules[i], data, pipeline->num_modules, &module_param_id, &picked_hash);

        if (lookup_result == COST_MODEL_LOOKUP_RESULT.NOT_FOUND)
        {
            set_error_param(INTERNAL_RUN_NOT_FOUND);
            return FAILURE;
        }

        err_current_module = i + 1;
        ProcessFunction module_function = pipeline->modules[i].module_function;
        ModuleParameterList *module_config = &module_parameter_lists[module_param_id];

        // measure time to execute the module
        struct timespec start, end;
        long elapsed_ns = 0;
        if (lookup_result == FOUND_NOT_CACHED)
        {
            clock_gettime(CLOCK_MONOTONIC, &start);

            // Get starting energy reading
            uint32_t start_energy = 0;
            if (param_pull_single(&mock_energy_nj, 0, CSP_PRIO_HIGH, 0, MOCK_ENERGY_NODE_ADDR, 500, 2) != 0)
            {
                fprintf(stderr, "Failed to pull start energy reading\n");
                start_energy = 0;
            }
            else
            {
                start_energy = param_get_uint32(&mock_energy_nj);
            }
        }

        int module_status = execute_module_in_process(module_function, data, module_config);

        uint32_t energy_cost = 0;

        if (lookup_result == FOUND_NOT_CACHED)
        {
            // measure time to execute the module
            clock_gettime(CLOCK_MONOTONIC, &end);
            // Get ending energy reading
            uint32_t end_energy = 0;
            if (param_pull_single(&mock_energy_nj, 0, CSP_PRIO_HIGH, 0, MOCK_ENERGY_NODE_ADDR, 500, 2) != 0)
            {
                fprintf(stderr, "Failed to pull end energy reading\n");
                end_energy = 0;
            }
            else
            {
                end_energy = param_get_uint32(&mock_energy_nj);
            }

            // Calculate energy cost (0 if readings failed)
            energy_cost = (start_energy && end_energy) ? (end_energy - start_energy) : 0;

            elapsed_ns = (end.tv_sec - start.tv_sec) * 1e9 + (end.tv_nsec - start.tv_nsec);
        }

        if (module_status == FAILURE)
        {
            /* Close all active pipes */
            close(output_pipe[0]); // Close the read end of the pipe
            close(output_pipe[1]); // Close the write end of the pipe
            close(error_pipe[0]);
            close(error_pipe[1]);
            return FAILURE;
        }

        if (lookup_result == FOUND_NOT_CACHED)
        {
            // Store both latency and energy cost in cache
            cache_insert(cost_cache, picked_hash, elapsed_ns, energy_cost);
        }

        ImageBatch result;
        int res = read(output_pipe[0], &result, sizeof(result)); // Read the result from the pipe
        if (res == FAILURE)
        {
            set_error_param(PIPE_READ);
            return FAILURE;
        }
        if (res == 0)
        {
            set_error_param(PIPE_EMPTY);
            return FAILURE;
        }

        data->num_images = result.num_images;
        data->batch_size = result.batch_size;
        data->pipeline_id = result.pipeline_id;
        data->priority = result.priority;
        data->progress = result.progress;
        strcpy(data->uuid, result.uuid);
        strcpy(data->filename, result.filename);
    }

    /* Close communication pipes */
    close(output_pipe[0]); // Close the read end of the pipe
    close(output_pipe[1]); // Close the write end of the pipe
    close(error_pipe[0]);
    close(error_pipe[1]);

    return SUCCESS;
}

int get_pipeline_by_id(int pipeline_id, Pipeline **pipeline)
{
    for (size_t i = 0; i < MAX_PIPELINES; i++)
    {
        if (pipelines[i].pipeline_id == pipeline_id)
        {
            *pipeline = &pipelines[i];
            return SUCCESS;
        }
    }
    set_error_param(INTERNAL_PID_NOT_FOUND);
    return FAILURE;
}

int get_pipeline_length(int pipeline_id)
{
    for (size_t i = 0; i < MAX_PIPELINES; i++)
    {
        if (pipelines[i].pipeline_id == pipeline_id)
        {
            return pipelines[i].num_modules;
        }
    }
    set_error_param(INTERNAL_PID_NOT_FOUND);
    return FAILURE;
}

int load_pipeline_and_execute(ImageBatch *input_batch)
{
    // Execute the pipeline with parameter values
    Pipeline *pipeline;
    if (get_pipeline_by_id(input_batch->pipeline_id, &pipeline) == FAILURE)
        return FAILURE;

    err_current_pipeline = pipeline->pipeline_id;

    return execute_pipeline(pipeline, input_batch);
}

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
        // printf("Pipeline executed successfully\n");
        // logger_log(logger, LOG_INFO, "Pipeline executed successfully");

        int pipeline_length = get_pipeline_length(input_batch->pipeline_id);
        if (pipeline_length == FAILURE)
        {
            printf("Error getting pipeline length\n");
            // logger_log(logger, LOG_ERROR, "Error getting pipeline length");
            return;
        }
        if (input_batch->progress == pipeline_length)
        {
            printf("Pipeline fully executed successfully\n");
            // logger_log(logger, LOG_INFO, "Pipeline executed successfully");

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
                set_error_param(MMAP_ATTACH);
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
            //     // logger_log(logger, LOG_ERROR, "Error deleting files");
            // }
        }
        else
        {
            printf("Pipeline partially executed successfully\n");
            // logger_log(logger, LOG_INFO, "Pipeline executed partially");

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

    ingest_pq = createPriorityQueue("/usr/share/dipp/queue_file");
    partially_processed_pq = createPriorityQueue("/usr/share/dipp/partially_processed_queue_file");

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
