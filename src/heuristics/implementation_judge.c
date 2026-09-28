#include "heuristics.h"
#include "image_batch.h"
#include "dipp_config.h"
#include "cost_store.h"
#include "cost_model.h"
#include "pipeline_config.pb-c.h"
#include "utils/minitrace.h"
#include "battery_simulator.h"

// Decide whether the module effort level fulfills the latency and energy requirements.
// The prediction comes from cost_model_predict (measured point at this input
// size, else the fitted model, else the YAML hint). Returns FOUND_CACHED when
// the store answered and the requirements are met, FOUND_NOT_CACHED when the
// hint answered and they are met, NOT_FOUND otherwise.
COST_MODEL_LOOKUP_RESULT judge_implementation(EffortLevel effort, Module *module, ImageBatch *data, int64_t latency_requirement, float energy_requirement, int *module_param_id, uint32_t *picked_hash, bool is_lowest_effort)
{
    MTR_BEGIN_FUNC_I("effort_level", effort);
    int32_t module_id = -1;

    switch (effort)
    {
    case EFFORT_LEVEL__DEFAULT:
        printf("This function should not be called with DEFAULT effort level\n");
        MTR_END_FUNC_I("result", NOT_FOUND);
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
        MTR_END_FUNC_I("result", NOT_FOUND);
        return NOT_FOUND;
    }

    if (module_id == -1)
    {
        // printf("Module %s does not have an implementation for effort level %d\n", module->module_name, effort);
        MTR_END_FUNC_I("result", NOT_FOUND);
        return NOT_FOUND;
    }

    ModuleParameterList *module_config = &module_parameter_lists[module_id];

    /* picked_hash is the CONFIG hash now (Stage 5a): the store is keyed by it
       plus the input size, not by a batch fingerprint. */
    *picked_hash = module_config->hash;

    if (is_lowest_effort)
    {
        // In lowest effort, we ignore latency requirements and only make sure that energy fits
        // This is in the case when we have already missed the deadline and want to just finish processing
        latency_requirement = INT64_MAX;
    }

    uint32_t latency;
    float energy;
    CostSource src = cost_model_predict(module_config, data->num_images, &latency, &energy);

    if ((int64_t)latency <= latency_requirement && energy <= energy_requirement)
    {
        *module_param_id = module_id;
        COST_MODEL_LOOKUP_RESULT r = (src == COST_SRC_HINT) ? FOUND_NOT_CACHED : FOUND_CACHED;
        MTR_END_FUNC_I("result", r);
        return r;
    }

    // Module does not fulfill the requirements
    MTR_END_FUNC_I("result", NOT_FOUND);
    return NOT_FOUND;
}