#include "heuristics.h"
#include "image_batch.h"
#include "dipp_config.h"
#include "cost_store.h"
#include "murmur_hash.h"
#include "pipeline_config.pb-c.h"

// Decide whether the module effort level fulfills the latency and energy requirements.
// It calculates a hash of the image batch metadata and module, and performs a lookup in the cost model.
// It returns FOUND_CACHED if a matching entry is found in the cost model cache and it fulfills
// the latency and energy requirements, FOUND_NOT_CACHED if no matching entry is found but the
// default latency and energy values fit within the requirements, or NOT_FOUND in case the module
// effort level was not found or does not fulfill the requirements.
COST_MODEL_LOOKUP_RESULT judge_implementation(EffortLevel effort, Module *module, ImageBatch *data, int latency_requirement, int energy_requirement, int *module_param_id, uint32_t *picked_hash)
{
    int32_t module_id = -1;

    switch (effort)
    {
    case EFFORT_LEVEL__DEFAULT:
        printf("This function should not be called with DEFAULT effort level\n");
        return NOT_FOUND;
        break;
    case EFFORT_LEVEL__LOW:
        module_id = module->low_effort_param_id;
        break;
    case EFFORT_LEVEL__MEDIUM:
        module_id = module->medium_effort_param_id;
        break;
    case EFFORT_LEVEL__HIGH:
        module_id = module->high_effort_param_id;
        break;
    default:
        printf("Unknown effort level\n");
        return NOT_FOUND;
    }

    if (module_id == -1)
    {
        printf("Module %s does not have an implementation for effort level %d\n", module->module_name, effort);
        return NOT_FOUND;
    }

    ModuleParameterList *module_config = &module_parameter_lists[module_id];

    uint32_t param_hash = module_config->hash;
    *picked_hash = murmur3_batch_fingerprint(data, param_hash);

    uint16_t latency, energy;
    if (cost_store_impl->lookup(cost_store, *picked_hash, &latency, &energy) != -1)
    {
        if (latency <= latency_requirement && energy <= energy_requirement)
        {
            *module_param_id = module_id;
            return FOUND_CACHED;
        }
    }
    else
    {
        latency = module_config->latency_cost;
        energy = module_config->energy_cost;

        // if not provided by the user, use the default values
        if (latency == 0)
            latency = DEFAULT_EFFORT_LATENCY;
        if (energy == 0)
            energy = DEFAULT_EFFORT_ENERGY;

        if (latency <= latency_requirement && energy <= energy_requirement)
        {
            *module_param_id = module_id;
            return FOUND_NOT_CACHED;
        }
    }

    // Module does not fulfill the requirements
    return NOT_FOUND;
}