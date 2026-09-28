#include "heuristics.h"
#include "image_batch.h"
#include "cost_store.h"
#include "cost_model.h"
#include "utils/minitrace.h"
#include "battery_simulator.h"

COST_MODEL_LOOKUP_RESULT get_default_implementation(Module *module, ImageBatch *data, int64_t latency_requirement, float energy_requirement, int *module_param_id, uint32_t *picked_hash)
{
    MTR_BEGIN_FUNC_C("effort_level", "default");
    ModuleParameterList *module_config = &module_parameter_lists[module->default_effort_param_id];
    *picked_hash = module_config->hash;

    // For default effort, we only check energy requirement
    // This is our only option for a module, therefore we
    // have to execute it once we pass the energy requirement
    (void)latency_requirement;
    uint32_t latency;
    float energy;
    CostSource src = cost_model_predict(module_config, data->num_images, &latency, &energy);
    if (energy <= energy_requirement)
    {
        *module_param_id = module->default_effort_param_id;
        COST_MODEL_LOOKUP_RESULT r = (src == COST_SRC_HINT) ? FOUND_NOT_CACHED : FOUND_CACHED;
        MTR_END_FUNC_I("result", r);
        return r;
    }
    MTR_END_FUNC_I("result", NOT_FOUND);
    return NOT_FOUND;
}