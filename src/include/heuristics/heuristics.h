#ifndef HEURISTICS_H
#define HEURISTICS_H

#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include "image_batch.h"
#include "pipeline_config.pb-c.h"
#include "cost_store.h"

#define DEFAULT_EFFORT_LATENCY 3000
#define DEFAULT_EFFORT_ENERGY 3.0f
#define LOW_EFFORT_LATENCY 2000
#define LOW_EFFORT_ENERGY 2.0f
#define MEDIUM_EFFORT_LATENCY 3000
#define MEDIUM_EFFORT_ENERGY 3.0f
#define HIGH_EFFORT_LATENCY 4000
#define HIGH_EFFORT_ENERGY 4.0f

#define BEST_EFFORT_MAX_LATENCY_MEDIUM_EFFORT 3000000 // 3 seconds maximum per module latency to be considered for medium effort
#define BEST_EFFORT_MAX_LATENCY_LOW_EFFORT 1000000    // 1 second maximum per module latency to be considered for low effort

typedef enum HEURISTIC_TYPE
{
    LOWEST_EFFORT = 0,
    BEST_EFFORT = 1,
} HEURISTIC_TYPE;

typedef struct Heuristic
{
    // Pick a module effort level based on the currently used heuristic.
    // The module_param_id will be populated with the ID of the picked module.
    // The picked_hash will be populated with the hash of the image batch metadata
    // and module parameters. This is further used to populate cost model after the first execution
    COST_MODEL_LOOKUP_RESULT (*heuristic_function)(Module *module, ImageBatch *data, size_t num_modules, int *module_param_id, uint32_t *picked_hash);
} Heuristic;

/* Updated prototypes: latency is uint32_t (microseconds), energy is float */
COST_MODEL_LOOKUP_RESULT get_default_implementation(Module *module, ImageBatch *data, uint32_t latency_requirement, float energy_requirement, int *module_param_id, uint32_t *picked_hash);
COST_MODEL_LOOKUP_RESULT judge_implementation(EffortLevel effort, Module *module, ImageBatch *data, uint32_t latency_requirement, float energy_requirement, int *module_param_id, uint32_t *picked_hash, bool is_lowest_effort);

extern Heuristic best_effort_heuristic;
extern Heuristic lowest_effort_heuristic;

#endif // HEURISTICS_H