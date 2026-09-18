#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/resource.h>
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


/* Resource sampling for C9 (deployment envelope) and C12 (eMMC wear).
 *
 * Nothing else in the system records memory or storage traffic, so both of
 * those analyses had no data source at all. Sampling here puts them on the same
 * per-execution record as everything else, which means one parser and one CSV.
 *
 * Cost is two small /proc reads and a syscall against modules that run for
 * 150 ms to 9 s, so it is not measurable in the latency figures.
 *
 * io_write_bytes is bytes actually sent to the block device, which is exactly
 * what the wear calculation needs -- page-cache writes that never reach storage
 * would flatter it.
 */
typedef struct ResourceSample
{
    long rss_kb;             /* current resident set */
    long peak_rss_kb;        /* this process, high-water */
    long child_peak_rss_kb;  /* largest reaped child -- the module processes */
    unsigned long long io_read_bytes;
    unsigned long long io_write_bytes;
} ResourceSample;

static void sample_resources(ResourceSample *out)
{
    memset(out, 0, sizeof(*out));

    FILE *f = fopen("/proc/self/statm", "r");
    if (f)
    {
        unsigned long size_pages = 0, resident_pages = 0;
        if (fscanf(f, "%lu %lu", &size_pages, &resident_pages) == 2)
        {
            long ps = sysconf(_SC_PAGESIZE);
            if (ps > 0)
                out->rss_kb = (long)((resident_pages * (unsigned long)ps) / 1024UL);
        }
        fclose(f);
    }

    struct rusage ru;
    if (getrusage(RUSAGE_SELF, &ru) == 0)
        out->peak_rss_kb = ru.ru_maxrss; /* kB on Linux */
    if (getrusage(RUSAGE_CHILDREN, &ru) == 0)
        out->child_peak_rss_kb = ru.ru_maxrss;

    f = fopen("/proc/self/io", "r");
    if (f)
    {
        char line[128];
        while (fgets(line, sizeof(line), f))
        {
            unsigned long long v;
            if (sscanf(line, "read_bytes: %llu", &v) == 1)
                out->io_read_bytes = v;
            else if (sscanf(line, "write_bytes: %llu", &v) == 1)
                out->io_write_bytes = v;
        }
        fclose(f);
    }
}

/* Sanity-check a module's result against the batch it was given.
 *
 * A module may change: num_images, batch_size, progress (to exactly its own
 * index), shmid (STORAGE_MEM: a new or the same segment) and filename
 * (STORAGE_MMAP: a new file under the data directory). It may not change
 * the identity of the batch -- pipeline_id, priority, uuid -- and every
 * string it returns must be terminated inside its buffer. A module that
 * keeps nothing returns num_images 0 and batch_size 0; that is a legitimate
 * outcome (open ocean, full cloud), not an error. */
static int validate_module_result(const ImageBatch *in, const ImageBatch *res, size_t module_index)
{
    const char *why = NULL;
    if (memchr(res->uuid, '\0', sizeof(res->uuid)) == NULL)
        why = "uuid not terminated";
    else if (strcmp(res->uuid, in->uuid) != 0)
        why = "uuid changed";
    else if (res->pipeline_id != in->pipeline_id)
        why = "pipeline_id changed";
    else if (res->priority != in->priority)
        why = "priority changed";
    else if (res->progress != (int)module_index)
        why = "progress is not this module's index";
    else if (res->num_images < 0 || res->batch_size < 0)
        why = "negative num_images or batch_size";
    else if ((res->num_images == 0) != (res->batch_size == 0))
        why = "num_images and batch_size disagree about emptiness";
    else if (res->batch_size > 0 && res->num_images > res->batch_size)
        why = "more images than bytes";
    else if (memchr(res->filename, '\0', sizeof(res->filename)) == NULL)
        why = "filename not terminated";
    else if (in->storage_mode == STORAGE_MMAP && res->batch_size > 0 &&
             strncmp(res->filename, "/usr/share/dipp/data/", 21) != 0)
        why = "filename outside the data directory";
    else if (in->storage_mode == STORAGE_MEM && res->batch_size > 0 && res->shmid < 0)
        why = "no shared-memory segment for a non-empty batch";
    if (why)
    {
        printf("MODULE_RESULT_INVALID uuid=%s module_index=%zu reason=%s "
               "num_images=%d batch_size=%d progress=%d shmid=%d\n",
               in->uuid, module_index, why, res->num_images, res->batch_size,
               res->progress, res->shmid);
        return -1;
    }
    return 0;
}

// Execute the pipeline on the given batch. It picks up from the possibly partially executed state,
// and for each module it picks the best effort level that fulfills the requirements based on the current state.
// If no such effort level is found, it stops the execution and returns an error.
int execute_pipeline(Pipeline *pipeline, ImageBatch *data)
{
    MTR_BEGIN_FUNC();

    printf("Starting pipeline execution from module %d out of %zu modules\n", data->progress + 1, pipeline->num_modules);

    for (size_t i = data->progress + 1; i < pipeline->num_modules; ++i)
    {
        MTR_BEGIN_I(__FILE__, "execute_module_loop", "module_index", i);
        int module_param_id = -1;
        uint32_t picked_hash;

        printf("Starting the execution of %ldth module\n", i);

        // printf("Looking up the best param_id using heuristic\r\n");
        // pick the module effort level using the currently set heuristic
        /* A batch on its one retry runs every remaining stage at the lowest
           effort, whatever the heuristic would pick (quarantine.h). */
        Heuristic *h = retry_lowest_effort ? &lowest_effort_heuristic : current_heuristic;
        COST_MODEL_LOOKUP_RESULT lookup_result = h->heuristic_function(&pipeline->modules[i], data, pipeline->num_modules, &module_param_id, &picked_hash);
        // printf("Got back a param_id=%d\r\n", module_param_id);

        // No new progress can be made, as no module fulfills the requirements
        if (lookup_result == NOT_FOUND)
        {
            // printf("No matching module found. No effort level fulfills the requirements\r\n");
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
        ImageBatch result;
        int module_status = execute_module_in_process(module_function, data, module_config, &result);
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

        // error encountered: the launcher has recorded it
        if (module_status == -1)
        {
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
            ResourceSample res;
            sample_resources(&res);
            printf("EXEC ts_us=%lld uuid=%s pipeline=%d module_index=%zu module=%s param_id=%d "
                   "num_images=%d batch_bytes=%d cached=%d "
                   "latency_pred_us=%u latency_meas_us=%ld "
                   "energy_pred_uwh=%u energy_debited_uwh=%.3f "
                   "battery_wh=%.6f deadline_us=%lld slack_us=%lld "
                   "ingest_depth=%ld partial_depth=%ld heuristic=%s "
                   "rss_kb=%ld peak_rss_kb=%ld child_peak_rss_kb=%ld "
                   "io_read_bytes=%llu io_write_bytes=%llu\n",
                   (long long)now_us, data->uuid, data->pipeline_id, i,
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
                   (current_heuristic == &best_effort_heuristic) ? "BEST_EFFORT" : "LOWEST_EFFORT",
                   res.rss_kb, res.peak_rss_kb, res.child_peak_rss_kb,
                   res.io_read_bytes, res.io_write_bytes);
        }

        /* The result came from a child process, i.e. from a module that may
           have crashed halfway through writing it or that is simply wrong.
           Nothing below may trust it: every field is checked against what this
           execution is allowed to change before it replaces the batch. The old
           code strcpy'd the child's uuid and filename into fixed buffers and
           took num_images/batch_size/shmid as given. */
        if (validate_module_result(data, &result, i) != 0)
        {
            set_error_param(MODULE_RESULT_INVALID);
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
        data->progress = result.progress;
        data->shmid = result.shmid;
        /* pipeline_id, priority and uuid are the daemon's, not the module's:
           validate_module_result has checked the module left them alone. */
        strncpy(data->filename, result.filename, sizeof(data->filename) - 1);
        data->filename[sizeof(data->filename) - 1] = '\0';

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