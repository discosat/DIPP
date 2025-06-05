#ifndef HEURISTICS_H
#define HEURISTICS_H

#include <stdio.h>
#include "dipp_process.h"
#include "pipeline_config.pb-c.h"

typedef enum HEURISTIC
{
    LOWEST_EFFORT = 0,
    BEST_EFFORT = 1,
} HEURISTIC;

typedef struct
{
    COST_MODEL_LOOKUP_RESULT (*heuristic_function)(Module *module, ImageBatch *data, size_t num_modules, int *module_param_id, uint32_t *picked_hash);
} Heuristic;

COST_MODEL_LOOKUP_RESULT get_default_implementation(Module *module, ImageBatch *data, int latency_requirement, int energy_requirement, int *module_param_id, uint32_t *picked_hash);
COST_MODEL_LOOKUP_RESULT judge_implementation(EffortLevel effort, Module *module, ImageBatch *data, int latency_requirement, int energy_requirement, int *module_param_id, uint32_t *picked_hash);

extern Heuristic best_effort_heuristic;
extern Heuristic lowest_effort_heuristic;

#endif // HEURISTICS_H