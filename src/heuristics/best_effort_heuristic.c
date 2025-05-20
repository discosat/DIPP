#include "heuristics.h"
#include "dipp_process.h"
#include "pipeline_config.pb-c.h"

COST_MODEL_LOOKUP_RESULT get_best_effort_implementation_config(Module *module, ImageBatch *data, int latency_requirement, int energy_requirement, int *module_param_id, uint32_t *picked_hash)
{
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