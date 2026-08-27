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
#include "dipp_process.h"
#include "priority_queue.h"
#include <time.h>

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

        /* Always time the module. Previously this ran only on a cache miss, so
           499 of every 500 executions produced no latency sample at all. */
        struct timespec start, end;
        long elapsed_us = 0;
        clock_gettime(CLOCK_MONOTONIC, &start);

        // printf("Starting execution in process\r\n");
        int module_status = execute_module_in_process(module_function, data, module_config);
        // printf("Finished execution\r\n");

        clock_gettime(CLOCK_MONOTONIC, &end);
        elapsed_us = (end.tv_sec - start.tv_sec) * 1000000L + (end.tv_nsec - start.tv_nsec) / 1000L;

        /* Energy debited to the battery for THIS execution.
           Sources, in order of preference:
             - the cost store, once an entry exists for this (module, config,
               input size) -- seeded from the offline Otii characterisation;
             - otherwise the YAML hint, which is all that is available on the
               first execution of a given key.
           This is deliberately a separate path from the value the heuristic
           uses to predict: the store's estimate drives selection, this drives
           the battery, and C7e needs to perturb one without the other. */
        float energy_cost;
        {
            uint32_t cached_latency_us;
            float cached_energy_uwh;
            if (cost_store_impl->lookup(cost_store, picked_hash,
                                        &cached_latency_us, &cached_energy_uwh) != -1)
                energy_cost = cached_energy_uwh;
            else
                energy_cost = (float)module_config->energy_cost;
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
            // First execution of this key: record what it actually cost.
            printf("Inserting into cache. Latency=%ld us, Energy=%.2f uWh\n", elapsed_us, energy_cost);
            cost_store_impl->insert(cost_store, picked_hash, elapsed_us, energy_cost);
            MTR_INSTANT_I(__FILE__, "latency cache update", "latency_us", (int)elapsed_us);
            MTR_INSTANT_I(__FILE__, "energy cache update", "energy_uwh", (int)energy_cost);
        }

        /* Charge the battery for EVERY execution. This used to sit inside the
           cache-miss branch above, so a 100-batch run drew energy 19 times
           instead of 500 and "mission completion under energy constraints" was
           measuring an almost-unloaded battery. */
        put_load_on_battery(energy_cost);
        MTR_COUNTER("main", "energy_debited_uwh", (int)energy_cost);

        /* One structured record per module execution.
           Deliberately a plain greppable line rather than a trace event:
           minitrace carries at most one argument per event, and this must
           survive a truncated or unflushed trace. B4 parses these, keyed to
           RUN.json, to regenerate the tables. */
        {
            struct timespec now_ts;
            clock_gettime(CLOCK_MONOTONIC, &now_ts);
            int64_t now_us = (int64_t)now_ts.tv_sec * 1000000 + (int64_t)now_ts.tv_nsec / 1000;
            printf("EXEC uuid=%s pipeline=%d module_index=%zu module=%s param_id=%d "
                   "num_images=%d batch_bytes=%d cached=%d "
                   "latency_pred_us=%u latency_meas_us=%ld "
                   "energy_pred_uwh=%u energy_debited_uwh=%.3f "
                   "battery_wh=%.6f deadline_us=%lld slack_us=%lld "
                   "ingest_depth=%ld partial_depth=%ld heuristic=%s\n",
                   data->uuid, data->pipeline_id, i,
                   pipeline->modules[i].module_name ? pipeline->modules[i].module_name : "?",
                   module_param_id,
                   data->num_images, data->batch_size,
                   (lookup_result == FOUND_CACHED) ? 1 : 0,
                   module_config->latency_cost, elapsed_us,
                   module_config->energy_cost, energy_cost,
                   get_battery_level_wh(),
                   (long long)data->priority, (long long)(data->priority - now_us),
                   pq_impl ? (long)pq_impl->get_queue_size(ingest_pq) : -1L,
                   pq_impl ? (long)pq_impl->get_queue_size(partially_processed_pq) : -1L,
                   (current_heuristic == &best_effort_heuristic) ? "BEST_EFFORT" : "LOWEST_EFFORT");
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
        char previous_filename[sizeof(data->filename)];
        strncpy(previous_filename, data->filename, sizeof(previous_filename) - 1);
        previous_filename[sizeof(previous_filename) - 1] = '\0';

        data->num_images = result.num_images;
        data->batch_size = result.batch_size;
        data->pipeline_id = result.pipeline_id;
        data->priority = result.priority;
        data->progress = result.progress;
        data->shmid = result.shmid;
        strcpy(data->uuid, result.uuid);
        strcpy(data->filename, result.filename);

        /* Checkpoint after EVERY module, not once per pipeline.
         *
         * The module has already msync'd its output, so the successor is on
         * storage. Recording (progress, filename) in the queue now makes that
         * durable too, and only then is the predecessor safe to unlink.
         *
         * Committing once per pipeline instead -- which is what this used to do
         * -- left a window spanning the whole pipeline where the queue entry
         * named a file the previous module had already deleted. A kill in that
         * window stranded the batch: on restart the first module could not open
         * its input and the batch was dropped. Measured at 9 of 20 batches lost
         * across 20 kill cycles.
         *
         * Ordering is what matters: commit the successor, THEN drop the
         * predecessor. The reverse loses the batch; this way the worst case is
         * an unreferenced file, which the startup orphan scan reclaims.
         */
        if (data->storage_mode == STORAGE_MMAP && current_batch_source != NULL &&
            pq_impl != NULL && pq_impl->commit_head != NULL)
        {
            pq_impl->commit_head((PriorityQueue *)current_batch_source, data);

            if (previous_filename[0] != '\0' &&
                strcmp(previous_filename, data->filename) != 0)
            {
                remove(previous_filename);
            }
        }

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