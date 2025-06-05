#include "heuristics.h"
#include "dipp_process.h"
#include "pipeline_config.pb-c.h"

COST_MODEL_LOOKUP_RESULT get_best_effort_implementation_config(Module *module, ImageBatch *data, size_t num_modules, int *module_param_id, uint32_t *picked_hash)
{
    struct timespec time;
    if (clock_gettime(CLOCK_MONOTONIC, &time) < 0)
    {
        printf("Error getting time\n");
        return NOT_FOUND;
    }

    size_t num_modules_left = num_modules - data->progress - 1; // number of modules left to process
    // TODO: Check if the -1 is correct

    int latency_requirement = (data->priority - time.tv_sec) / num_modules_left; // how much time is there to process the rest of the stages on average
    // TODO: Add a scaling factor to the lateny requirement (we should not use the whole time left to process the rest of the modules)
    int energy_requirement = 0; // TODO: change this to the current energy battery level - MIN_BATTERY_LEVEL

    if (module->default_effort_param_id != -1)
    {
        return get_default_implementation(module, data, latency_requirement, energy_requirement, module_param_id, picked_hash);
    }
    else
    {
        // start from the heavy and go down (the first that fulfils the requiments is the one to use)
        COST_MODEL_LOOKUP_RESULT result = judge_implementation(EFFORT_LEVEL__HIGH, module, data, latency_requirement, energy_requirement, module_param_id, picked_hash);
        if (result == FOUND_NOT_CACHED || result == FOUND_CACHED)
        {
            return result;
        }
        COST_MODEL_LOOKUP_RESULT result = judge_implementation(EFFORT_LEVEL__MEDIUM, module, data, latency_requirement, energy_requirement, module_param_id, picked_hash);
        if (result == FOUND_NOT_CACHED || result == FOUND_CACHED)
        {
            return result;
        }
        COST_MODEL_LOOKUP_RESULT result = judge_implementation(EFFORT_LEVEL__LOW, module, data, latency_requirement, energy_requirement, module_param_id, picked_hash);
        if (result == FOUND_NOT_CACHED || result == FOUND_CACHED)
        {
            return result;
        }
        return NOT_FOUND;
    }
}

Heuristic best_effort_heuristic = {
    .heuristic_function = get_best_effort_implementation_config,
};