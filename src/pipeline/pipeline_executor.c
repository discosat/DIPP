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
#include "utils/minitrace.h"
#include "battery_simulator.h"

// Execute the pipeline on the given batch. It picks up from the possibly partially executed state,
// and for each module it picks the best effort level that fulfills the requirements based on the current state.
// If no such effort level is found, it stops the execution and returns an error.
int execute_pipeline(Pipeline *pipeline, ImageBatch *data)
{
    MTR_BEGIN_FUNC();
    /* Initiate communication pipes */
    if (pipe(output_pipe) == -1 || pipe(error_pipe) == -1)
    {
        set_error_param(PIPE_CREATE);
        return -1;
    }

    printf("Starting pipeline execution from module %d out of %zu modules\n", data->progress + 1, pipeline->num_modules);

    for (size_t i = data->progress + 1; i < pipeline->num_modules; ++i)
    {
        MTR_BEGIN_I(__FILE__, "execute_module_loop", "module_index", i);
        int module_param_id = -1;
        uint32_t picked_hash;

        printf("Starting the execution of %ldth module\n", i);

        // printf("Looking up the best param_id using heuristic\r\n");
        // pick the module effort level using the currently set heuristic
        COST_MODEL_LOOKUP_RESULT lookup_result = current_heuristic->heuristic_function(&pipeline->modules[i], data, pipeline->num_modules, &module_param_id, &picked_hash);
        // printf("Got back a param_id=%d\r\n", module_param_id);

        // No new progress can be made, as no module fulfills the requirements
        if (lookup_result == NOT_FOUND)
        {
            // printf("No matching module found. No effort level fulfills the requirements\r\n");
            /* Close all active pipes */
            close(output_pipe[0]); // Close the read end of the pipe
            close(output_pipe[1]); // Close the write end of the pipe
            close(error_pipe[0]);
            close(error_pipe[1]);
            MTR_END(__FILE__, "execute_module_loop");
            MTR_END_FUNC();
            return 0;
        }

        err_current_module = i + 1;
        ProcessFunction module_function = pipeline->modules[i].module_function;
        // pick the module with selected effort level
        ModuleParameterList *module_config = &module_parameter_lists[module_param_id];

        // printf("Here is the module config: Num params=%zu, Hash=%u, Latency=%u, Energy=%u\r\n",
        //        module_config->n_parameters,
        //        module_config->hash,
        //        module_config->latency_cost,
        //        module_config->energy_cost);
        // for (size_t p = 0; p < module_config->n_parameters; ++p)
        // {
        //     ModuleParameter *param = module_config->parameters[p];
        //     printf("Param %zu: Name=%s, Type=%d, Value=", p, param->key, param->value_case);
        //     switch (param->value_case)
        //     {
        //     case BOOL_VALUE:
        //         printf("%d\r\n", param->bool_value);
        //         break;
        //     case INT_VALUE:
        //         printf("%d\r\n", param->int_value);
        //         break;
        //     case FLOAT_VALUE:
        //         printf("%f\r\n", param->float_value);
        //         break;
        //     case STRING_VALUE:
        //         printf("%s\r\n", param->string_value);
        //         break;
        //     default:
        //         printf("Unknown parameter type\r\n");
        //         break;
        //     }
        // }

        // measure time to execute the module
        struct timespec start, end;
        uint32_t start_energy = 0, end_energy = 0;
        long elapsed_us = 0;

        // the profiling information is not found, collect it here
        if (lookup_result == FOUND_NOT_CACHED)
        {
            clock_gettime(CLOCK_MONOTONIC, &start);

            // Get starting energy reading
            start_energy = get_energy_reading();
        }

        // printf("Starting execution in process\r\n");
        int module_status = execute_module_in_process(module_function, data, module_config);
        // printf("Finished execution\r\n");

        float energy_cost = 0;

        // the profiling information is not found, collect it here
        if (lookup_result == FOUND_NOT_CACHED)
        {
            // measure time to execute the module
            clock_gettime(CLOCK_MONOTONIC, &end);

            // Get ending energy reading
            // end_energy = get_energy_reading();

            // Calculate energy cost (0 if readings failed)
            // energy_cost = (start_energy && end_energy) ? (end_energy - start_energy) : 0;
            energy_cost = module_config->energy_cost; // Use estimated energy cost from module config for now

            elapsed_us = (end.tv_sec - start.tv_sec) * 1000000L + (end.tv_nsec - start.tv_nsec) / 1000L;
        }

        // error encountered, clean up
        if (module_status == -1)
        {
            /* Close all active pipes */
            close(output_pipe[0]); // Close the read end of the pipe
            close(output_pipe[1]); // Close the write end of the pipe
            close(error_pipe[0]);
            close(error_pipe[1]);
            MTR_END(__FILE__, "execute_module_loop");
            MTR_END_FUNC();
            return -1;
        }

        if (lookup_result == FOUND_NOT_CACHED)
        {
            // Store both latency and energy cost in cache
            printf("Inserting into cache. Latency=%ld us, Energy=%.2f uWh\n", elapsed_us, energy_cost);
            cost_store_impl->insert(cost_store, picked_hash, elapsed_us, energy_cost);
            MTR_INSTANT_I(__FILE__, "latency cache update", "latency_us", (int)elapsed_us);
            MTR_INSTANT_I(__FILE__, "energy cache update", "energy_uwh", (int)(energy_cost * SIMULATION_STEPS_PER_UPDATE));
            put_load_on_battery(energy_cost * SIMULATION_STEPS_PER_UPDATE); // scale to fit simulation step size
        }

        ImageBatch result;
        int res = read(output_pipe[0], &result, sizeof(result)); // Read the result from the pipe
        if (res == -1)
        {
            set_error_param(PIPE_READ);
            MTR_END(__FILE__, "execute_module_loop");
            MTR_END_FUNC();
            return -1;
        }
        if (res == 0)
        {
            set_error_param(PIPE_EMPTY);
            MTR_END(__FILE__, "execute_module_loop");
            MTR_END_FUNC();
            return -1;
        }

        // update the image batch metadata before the next module
        data->num_images = result.num_images;
        data->batch_size = result.batch_size;
        data->pipeline_id = result.pipeline_id;
        data->priority = result.priority;
        data->progress = result.progress;
        data->shmid = result.shmid;
        strcpy(data->uuid, result.uuid);
        strcpy(data->filename, result.filename);

        MTR_END(__FILE__, "execute_module_loop");
    }

    /* Close communication pipes */
    close(output_pipe[0]); // Close the read end of the pipe
    close(output_pipe[1]); // Close the write end of the pipe
    close(error_pipe[0]);
    close(error_pipe[1]);

    MTR_END_FUNC();

    return 0;
}

// Retrieve a pointer to the pipeline with the given ID.
// Set a corresponding error if pipeline ID not found.
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