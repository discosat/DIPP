#include "heuristics.h"
#include "image_batch.h"
#include "cost_store.h"
#include "murmur_hash.h"

COST_MODEL_LOOKUP_RESULT get_default_implementation(Module *module, ImageBatch *data, int latency_requirement, int energy_requirement, int *module_param_id, uint32_t *picked_hash)
{
    uint32_t param_hash = module_parameter_lists[module->default_effort_param_id].hash;
    *picked_hash = murmur3_batch_fingerprint(data, param_hash);

    uint16_t latency, energy;
    if (cost_store_impl->lookup(cost_cache, *picked_hash, &latency, &energy) != -1)
    {
        if (latency <= latency_requirement && energy <= energy_requirement)
        {
            *module_param_id = module->default_effort_param_id;
            return FOUND_CACHED;
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
            return FOUND_NOT_CACHED;
        }
    }
    return NOT_FOUND;
}