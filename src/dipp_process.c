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

static int output_pipe[2];     // Pipe for inter-process result communication
static int error_pipe[2];      // Pipe for inter-process error communication
static int is_interrupted = 0; // Flag to indicate if the process was interrupted

Logger *logger;
CostEntry *cost_cache;

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

COST_MODEL_LOOKUP_RESULT get_implementation_config(Module *module, ImageBatch *data, int latency_requirement, int energy_requirement, int *module_param_id, uint32_t *picked_hash, IMPLEMENTATION_PREFERENCE preference)
{
    if (module->default_effort_param_id != -1)
    {
        uint32_t param_hash = module_parameter_lists[module->default_effort_param_id].hash;
        *picked_hash = murmur3_batch_fingerprint(data, param_hash);

        uint16_t latency, energy;
        if (cache_lookup(cost_cache, *picked_hash, &latency, &energy) != -1)
        {
            if (latency <= latency_requirement && energy <= energy_requirement)
            {
                *module_param_id = module->default_effort_param_id;
                return COST_MODEL_LOOKUP_RESULT.FOUND_CACHED;
            }
        }
        else
        {
            latency = module_parameter_lists[module->default_effort_param_id].latency_cost;
            energy = module_parameter_lists[module->default_effort_param_id].energy_cost;

            // if not provided by the user, use the default values
            if (latency == 0)
                latency = DEFAULT_EFFORT_LATENCY;
            if (energy == 0)
                energy = DEFAULT_EFFORT_ENERGY;

            if (latency <= latency_requirement && energy <= energy_requirement)
            {
                *module_param_id = module->default_effort_param_id;
                return COST_MODEL_LOOKUP_RESULT.FOUND_NOT_CACHED;
            }
        }
        return COST_MODEL_LOOKUP_RESULT.NOT_FOUND;
    }
    else
    {
        // start from the heavy and go down (the first that fulfils the requiments is the one to use)
        switch (preference)
        {
        case LATENCY:
            // TODO: write the policy logic
            break;
        case ENERGY:
            // TODO: write the policy logic
            break;
        case BEST_EFFORT:
            // TODO: write the policy logic
            break;
        default:
            printf("Unknown implementation preference\n");
            break;
        }
    }
    return COST_MODEL_LOOKUP_RESULT.NOT_FOUND;
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
        // TODO: Decide which policy to use
        // TODO: add the default latency and energy cost to module params

        size_t module_param_id;
        uint32_t picked_hash;
        COST_MODEL_LOOKUP_RESULT lookup_result = get_implementation_config(&pipeline->modules[i], data, 0, 0, &module_param_id, &picked_hash, LATENCY);

        if (lookup_result == COST_MODEL_LOOKUP_RESULT.NOT_FOUND)
        {
            set_error_param(INTERNAL_RUN_NOT_FOUND);
            return FAILURE;
        }

        // TODO: handle the case where the implementation is not cached -> performance needs to be measured and cached

        err_current_module = i + 1;
        ProcessFunction module_function = pipeline->modules[i].module_function;
        ModuleParameterList *module_config = &module_parameter_lists[module_param_id];

        // measure time to execute the module
        struct timespec start, end;
        clock_gettime(CLOCK_MONOTONIC, &start);
        // TODO: get the starting energy

        int module_status = execute_module_in_process(module_function, data, module_config);

        clock_gettime(CLOCK_MONOTONIC, &end);
        // TODO: get the energy cost

        long elapsed_ns = (end.tv_sec - start.tv_sec) * 1e9 + (end.tv_nsec - start.tv_nsec);

        if (module_status == FAILURE)
        {
            /* Close all active pipes */
            close(output_pipe[0]); // Close the read end of the pipe
            close(output_pipe[1]); // Close the write end of the pipe
            close(error_pipe[0]);
            close(error_pipe[1]);
            return FAILURE;
        }

        // save the costs to the cost store if not already cached

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

        // TODO: Monitor in order to break early.
    }

    /* Close communication pipes */
    close(output_pipe[0]); // Close the read end of the pipe
    close(output_pipe[1]); // Close the write end of the pipe
    close(error_pipe[0]);
    close(error_pipe[1]);

    return SUCCESS;
}

void save_images(const char *filename_base, const ImageBatch *batch)
{
    uint32_t offset = 0;
    int image_index = 0;

    while (image_index < batch->num_images && offset < batch->batch_size)
    {
        uint32_t meta_size = *((uint32_t *)(batch->data + offset));
        offset += sizeof(uint32_t); // Move the offset to the start of metadata
        Metadata *metadata = metadata__unpack(NULL, meta_size, batch->data + offset);
        offset += meta_size; // Move offset to start of image

        char filename[20];
        sprintf(filename, "%s%d.raw", filename_base, image_index);

        FILE *filePtr;

        // Open the file in binary mode for writing
        filePtr = fopen(filename, "wb");
        if (filePtr == NULL)
        {
            fprintf(stderr, "Error opening file.\n");
            return;
        }

        // Write the byte array to the file
        fwrite(batch->data + offset, sizeof(unsigned char), metadata->size, filePtr);

        // Close the file
        fclose(filePtr);

        offset += metadata->size; // Move the offset to the start of the next image block

        image_index++;
    }
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
    // printf("Processing\n");
    // logger_log(logger, LOG_INFO, "Processing image batch");
    int pipeline_result = load_pipeline_and_execute(input_batch);

    if (pipeline_result == FAILURE)
    {
        // printf("Error processing image batch\n");
        // logger_log(logger, LOG_ERROR, "Error processing image batch");
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

/* Process one image batch from the message queue*/
void process_one(int do_wait)
{
    setup_cache_if_needed();

    ImageBatch datarcv;
    if (get_message_from_queue(&datarcv, do_wait) == SUCCESS)
        process(&datarcv);
}

/* Process all image batches in the message queue*/
void process_all(int do_wait)
{

    ImageBatch datarcv;
    while (get_message_from_queue(&datarcv, do_wait) == SUCCESS)
    {
        logger_log_print(logger, LOG_INFO, "Batch processing started");
        setup_cache_if_needed();
        logger_log_print(logger, LOG_INFO, "Cache rebuilt");
        process(&datarcv);
        logger_log_print(logger, LOG_INFO, "Batch processing started");
    }
}

typedef struct ProcessThreadArgs
{
    int all;
    int wait;
    param_t *param;
} ProcessThreadArgs;

atomic_int is_processing = ATOMIC_VAR_INIT(0);

void *process_thread(void *arg)
{
    ProcessThreadArgs *args = (ProcessThreadArgs *)arg;
    int all = args->all;
    int wait = args->wait;
    param_t *param = args->param;
    free(args);

    if (all)
        process_all(wait);
    else
        process_one(wait);

    /* Indicate that processing is finished */
    atomic_store(&is_processing, 0);
    param_set_uint8(param, 0);

    logger_log_print(logger, LOG_INFO, "Processing finished");
    logger_flush(logger);
    logger_destroy(logger);

    return NULL;
}

void callback_run(param_t *param, int index)
{
    uint8_t param_value = param_get_uint8(param);
    if (!param_value)
        return;

    /* Check whether a thread is currently processing */
    int expected = 0;
    if (!atomic_compare_exchange_strong(&is_processing, &expected, 1))
    {
        // another thread is already processing
        return;
    }

    const char *dir = "/home/root/logs/";
    char file_name[256];
    time_t t = time(NULL);
    snprintf(file_name, sizeof(file_name), "dipp_%ld.txt", t);

    char full_path[512];
    snprintf(full_path, sizeof(full_path), "%s%s", dir, file_name);

    logger = logger_create(full_path);

    logger_log_print(logger, LOG_INFO, "Processing started");

    /* Initialize thread variables */
    ProcessThreadArgs *args = malloc(sizeof(ProcessThreadArgs));
    if (args == NULL)
    {
        // atomically update is_processing
        atomic_store(&is_processing, 0);
        return;
    }

    args->all = param_value % 2 == 0;
    args->wait = param_value > 2;
    args->param = param;

    /* Execute pipeline on new thread, to allow callback to finish */
    pthread_t processing_thread;
    if (pthread_create(&processing_thread, NULL, process_thread, args) != 0)
    {
        // create thread failed
        free(args);
        atomic_store(&is_processing, 0);
        return;
    }

    pthread_detach(processing_thread);
    logger_log_print(logger, LOG_INFO, "Processing thread detatched.");
}

void process_images_loop()
{

    ingest_pq = createPriorityQueue("/usr/share/dipp/queue_file");
    partially_processed_pq = createPriorityQueue("/usr/share/dipp/partially_processed_queue_file");

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
