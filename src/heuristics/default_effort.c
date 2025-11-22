#include "heuristics.h"
#include "image_batch.h"
#include "cost_store.h"
#include "murmur_hash.h"
#include "utils/minitrace.h"
#include "battery_simulator.h"

COST_MODEL_LOOKUP_RESULT get_default_implementation(Module *module, ImageBatch *data, uint32_t latency_requirement, float energy_requirement, int *module_param_id, uint32_t *picked_hash)
{
    MTR_BEGIN_FUNC_C("effort_level", "default");
    uint32_t param_hash = module_parameter_lists[module->default_effort_param_id].hash;
    *picked_hash = murmur3_batch_fingerprint(data, param_hash);

    // For default effort, we only check energy requirement
    // This is our only option for a module, therefore we
    // have to execute it once we pass the energy requirement

    uint32_t latency;
    float energy;
    if (cost_store_impl->lookup(cost_store, *picked_hash, &latency, &energy) != -1)
    {
        // printf("Found in cost store with latency=%u and energy=%f\r\n", latency, energy);
        // scale to fit simulation step size
        energy = energy * SIMULATION_STEPS_PER_UPDATE;
        if (energy <= energy_requirement)
        {
            *module_param_id = module->default_effort_param_id;
            MTR_END_FUNC_I("result", FOUND_CACHED);
            return FOUND_CACHED;
        }
    }
    else
    {
        // printf("Did not find in cost store\r\n");
        energy = (float)module_parameter_lists[module->default_effort_param_id].energy_cost;

        if (energy == 0.0f)
            energy = (float)DEFAULT_EFFORT_ENERGY;

        // scale to fit simulation step size
        energy = energy * SIMULATION_STEPS_PER_UPDATE;

        if (energy <= energy_requirement)
        {
            *module_param_id = module->default_effort_param_id;
            MTR_END_FUNC_I("result", FOUND_NOT_CACHED);
            return FOUND_NOT_CACHED;
        }
    }
    MTR_END_FUNC_I("result", NOT_FOUND);
    return NOT_FOUND;
}