#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include "pipeline_executor.h"
#include "process_module.h"
#include "cost_store.h"
#include "heuristics.h"
#include "vmem_upload_local.h"
#include "telemetry.h"
#include "dipp_config.h"
#include "image_batch.h"
#include "dipp_error.h"

int execute_pipeline(Pipeline *pipeline, ImageBatch *data)
{
    /* Initiate communication pipes */
    if (pipe(output_pipe) == -1 || pipe(error_pipe) == -1)
    {
        set_error_param(PIPE_CREATE);
        return -1;
    }

    for (size_t i = data->progress + 1; i < pipeline->num_modules; ++i)
    {
        int module_param_id;
        uint32_t picked_hash;

        COST_MODEL_LOOKUP_RESULT lookup_result = current_heuristic->heuristic_function(&pipeline->modules[i], data, pipeline->num_modules, &module_param_id, &picked_hash);

        if (lookup_result == NOT_FOUND)
        {
            set_error_param(INTERNAL_RUN_NOT_FOUND);
            return -1;
        }

        err_current_module = i + 1;
        ProcessFunction module_function = pipeline->modules[i].module_function;
        ModuleParameterList *module_config = &module_parameter_lists[module_param_id];

        // measure time to execute the module
        struct timespec start, end;
        uint32_t start_energy = 0, end_energy = 0;
        long elapsed_ns = 0;
        if (lookup_result == FOUND_NOT_CACHED)
        {
            clock_gettime(CLOCK_MONOTONIC, &start);

            // Get starting energy reading
            start_energy = get_energy_reading();
        }

        int module_status = execute_module_in_process(module_function, data, module_config);

        uint32_t energy_cost = 0;

        if (lookup_result == FOUND_NOT_CACHED)
        {
            // measure time to execute the module
            clock_gettime(CLOCK_MONOTONIC, &end);

            // Get ending energy reading
            end_energy = get_energy_reading();

            // Calculate energy cost (0 if readings failed)
            energy_cost = (start_energy && end_energy) ? (end_energy - start_energy) : 0;

            elapsed_ns = (end.tv_sec - start.tv_sec) * 1e9 + (end.tv_nsec - start.tv_nsec);
        }

        if (module_status == -1)
        {
            /* Close all active pipes */
            close(output_pipe[0]); // Close the read end of the pipe
            close(output_pipe[1]); // Close the write end of the pipe
            close(error_pipe[0]);
            close(error_pipe[1]);
            return -1;
        }

        if (lookup_result == FOUND_NOT_CACHED)
        {
            // Store both latency and energy cost in cache
            cost_store_impl->insert(cost_cache, picked_hash, elapsed_ns, energy_cost);
        }

        ImageBatch result;
        int res = read(output_pipe[0], &result, sizeof(result)); // Read the result from the pipe
        if (res == -1)
        {
            set_error_param(PIPE_READ);
            return -1;
        }
        if (res == 0)
        {
            set_error_param(PIPE_EMPTY);
            return -1;
        }

        data->num_images = result.num_images;
        data->batch_size = result.batch_size;
        data->pipeline_id = result.pipeline_id;
        data->priority = result.priority;
        data->progress = result.progress;
        data->shmid = result.shmid;
        strcpy(data->uuid, result.uuid);
        strcpy(data->filename, result.filename);
    }

    /* Close communication pipes */
    close(output_pipe[0]); // Close the read end of the pipe
    close(output_pipe[1]); // Close the write end of the pipe
    close(error_pipe[0]);
    close(error_pipe[1]);

    return 0;
}

int get_pipeline_by_id(int pipeline_id, Pipeline **pipeline)
{
    for (size_t i = 0; i < MAX_PIPELINES; i++)
    {
        if (pipelines[i].pipeline_id == pipeline_id)
        {
            *pipeline = &pipelines[i];
            return 0;
        }
    }
    set_error_param(INTERNAL_PID_NOT_FOUND);
    return -1;
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
    return -1;
}

int load_pipeline_and_execute(ImageBatch *input_batch)
{
    // Execute the pipeline with parameter values
    Pipeline *pipeline;
    if (get_pipeline_by_id(input_batch->pipeline_id, &pipeline) == -1)
        return -1;

    err_current_pipeline = pipeline->pipeline_id;

    return execute_pipeline(pipeline, input_batch);
}