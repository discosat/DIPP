#include "heuristics.h"
#include <time.h>
#include <stdbool.h>
#include "image_batch.h"
#include "pipeline_config.pb-c.h"
#include "cost_store.h"
#include "utils/minitrace.h"
#include "battery_simulator.h"

COST_MODEL_LOOKUP_RESULT get_best_effort_implementation_config(Module *module, ImageBatch *data, size_t num_modules, int *module_param_id, uint32_t *picked_hash)
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

    uint32_t latency_requirement = (uint32_t)(((data->priority - time.tv_sec) * 1e6) / (int64_t)num_modules_left); // time left in microseconds divided by number of modules left
    float battery_level_wh = get_battery_level_wh();
    float energy_requirement = (battery_level_wh - BATTERY_SAFETY_MARGIN_WH) * 1000000.0f; // current battery level minus safety margin (microwatt-hours)

    MTR_COUNTER("main", "battery_level_uwh", (int)(battery_level_wh * 1000000.0f));

    MTR_INSTANT_I(__FILE__, "best_effort_heuristic", "latency_requirement (us)", latency_requirement);
    MTR_INSTANT_I(__FILE__, "best_effort_heuristic", "energy_requirement (uWh)", (int)energy_requirement);

    // printf("Number of modules left: %zu, Latency requirement: %u, Energy requirement: %f\n", num_modules_left, latency_requirement, energy_requirement);

    if (module->default_effort_param_id != -1)
    {
        MTR_END_FUNC();
        return get_default_implementation(module, data, latency_requirement, energy_requirement, module_param_id, picked_hash);
    }
    else
    {
        EffortLevel lowest_effort_level = EFFORT_LEVEL__HIGH;
        if (module->medium_effort_param_id != -1)
        {
            lowest_effort_level = EFFORT_LEVEL__MEDIUM;
        }
        if (module->low_effort_param_id != -1)
        {
            lowest_effort_level = EFFORT_LEVEL__LOW;
        }

        // start from the heavy and go down in the effort levels
        // (the first that fulfils the requirements is the one to use)
        COST_MODEL_LOOKUP_RESULT result = judge_implementation(EFFORT_LEVEL__HIGH, module, data, latency_requirement, energy_requirement, module_param_id, picked_hash, lowest_effort_level == EFFORT_LEVEL__HIGH);
        if (result == FOUND_NOT_CACHED || result == FOUND_CACHED)
        {
            MTR_END_FUNC();
            return result;
        }
        // only check medium if we already considered high and latency requirement is tight enough
        if (module->high_effort_param_id != -1 && latency_requirement < BEST_EFFORT_MAX_LATENCY_MEDIUM_EFFORT)
        {
            result = judge_implementation(EFFORT_LEVEL__MEDIUM, module, data, latency_requirement, energy_requirement, module_param_id, picked_hash, lowest_effort_level == EFFORT_LEVEL__MEDIUM);
            if (result == FOUND_NOT_CACHED || result == FOUND_CACHED)
            {
                MTR_END_FUNC();
                return result;
            }
        }
        // only check low if we already considered higher efforts and latency requirement is tight enough
        if ((module->high_effort_param_id != -1 || module->medium_effort_param_id != -1) && latency_requirement < BEST_EFFORT_MAX_LATENCY_LOW_EFFORT)
        {
            result = judge_implementation(EFFORT_LEVEL__LOW, module, data, latency_requirement, energy_requirement, module_param_id, picked_hash, lowest_effort_level == EFFORT_LEVEL__LOW);
            if (result == FOUND_NOT_CACHED || result == FOUND_CACHED)
            {
                MTR_END_FUNC();
                return result;
            }
        }
        // The effort levels are either empty or none of them fulfill the requirements
        MTR_END_FUNC();
        return NOT_FOUND;
    }
}

Heuristic best_effort_heuristic = {
    .heuristic_function = get_best_effort_implementation_config,
};