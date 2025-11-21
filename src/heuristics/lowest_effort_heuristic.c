#include "heuristics.h"
#include "image_batch.h"
#include "pipeline_config.pb-c.h"
#include "cost_store.h"
#include <time.h>
#include <stdbool.h>
#include "utils/minitrace.h"

COST_MODEL_LOOKUP_RESULT get_lowest_effort_implementation_config(Module *module, ImageBatch *data, size_t num_modules, int *module_param_id, uint32_t *picked_hash)
{
    MTR_BEGIN_FUNC();
    struct timespec time;
    if (clock_gettime(CLOCK_MONOTONIC, &time) < 0)
    {
        printf("Error getting time\n");
        MTR_END_FUNC();
        return NOT_FOUND;
    }

    size_t num_modules_left = num_modules - (data->progress + 1); // number of modules left to process

    /* latency in microseconds per remaining module */
    uint32_t latency_requirement = (uint32_t)(((data->priority - time.tv_sec) * 1e9) / (int64_t)num_modules_left); // time left in nanoseconds divided by number of modules left
    /* energy as float (e.g. battery remaining) */
    float energy_requirement = 1000000.0f; // TODO: change this to the current energy battery level - MIN_BATTERY_LEVEL

    // printf("Number of modules left: %zu, Latency requirement: %u, Energy requirement: %f\r\n", num_modules_left, latency_requirement, energy_requirement);

    // If there is only a single effort level, use that one
    if (module->default_effort_param_id != -1)
    {
        MTR_END_FUNC();
        return get_default_implementation(module, data, latency_requirement, energy_requirement, module_param_id, picked_hash);
    }
    else
    {
        EffortLevel lowest_effort_level = EFFORT_LEVEL__LOW;
        if (module->low_effort_param_id == -1)
        {
            lowest_effort_level = EFFORT_LEVEL__MEDIUM;
        }
        if (module->medium_effort_param_id == -1 && lowest_effort_level == EFFORT_LEVEL__LOW)
        {
            lowest_effort_level = EFFORT_LEVEL__HIGH;
        }

        // start from the lightest and go up in the effort levels
        // (the first that fulfils the requiments is the one to use)
        // printf("Checking the low effort\r\n");
        COST_MODEL_LOOKUP_RESULT result = judge_implementation(EFFORT_LEVEL__LOW, module, data, latency_requirement, energy_requirement, module_param_id, picked_hash, lowest_effort_level == EFFORT_LEVEL__LOW);
        if (result == FOUND_NOT_CACHED || result == FOUND_CACHED)
        {
            MTR_END_FUNC();
            return result;
        }
        // printf("Checking the medium effort\r\n");
        result = judge_implementation(EFFORT_LEVEL__MEDIUM, module, data, latency_requirement, energy_requirement, module_param_id, picked_hash, lowest_effort_level == EFFORT_LEVEL__MEDIUM);
        if (result == FOUND_NOT_CACHED || result == FOUND_CACHED)
        {
            MTR_END_FUNC();
            return result;
        }
        // printf("Checking the high effort\r\n");
        result = judge_implementation(EFFORT_LEVEL__HIGH, module, data, latency_requirement, energy_requirement, module_param_id, picked_hash, lowest_effort_level == EFFORT_LEVEL__HIGH);
        if (result == FOUND_NOT_CACHED || result == FOUND_CACHED)
        {
            MTR_END_FUNC();
            return result;
        }
        // The effort levels are either empty or none of them fulfill the requirements
        MTR_END_FUNC();
        return NOT_FOUND;
    }
}

Heuristic lowest_effort_heuristic = {
    .heuristic_function = get_lowest_effort_implementation_config,
};