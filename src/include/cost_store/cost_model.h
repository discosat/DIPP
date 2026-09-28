#ifndef COST_MODEL_H
#define COST_MODEL_H

#include <stdint.h>
#include "dipp_config.h"

/* The one place the scheduler's cost prediction comes from.
 *
 * cost_model_predict answers "what will this module config cost on an input
 * of size n" from, in order: a measured POINT at exactly n, the fitted MODEL
 * line, the YAML hint -- a positive measured value; dipp_config.c rejects a
 * slot whose hint is not. Which one answered is returned, and EXEC logs it as cost_src=.
 *
 * n is the batch's num_images as the module sees it: frames for demosaic,
 * tile and the monolith; tiles for classification, segmentation and the
 * encoder. That is what varies between executions and what the offline sweep
 * varied. */
typedef enum CostSource
{
    COST_SRC_NONE = 0,
    COST_SRC_POINT = 1,
    COST_SRC_MODEL = 2,
    COST_SRC_HINT = 3,
} CostSource;

CostSource cost_model_predict(const ModuleParameterList *cfg, int n, uint32_t *lat_us, float *en_uwh);

/* Fold a measured latency into the point at (cfg, n): an EMA on an existing
   point, or a new on-board point whose energy is the prediction that was
   made for it (`pred_en_uwh`) -- energy is never measured on-board. A no-op
   when DIPP_LATENCY_CORRECTION=0. */
void cost_model_observe_latency(const ModuleParameterList *cfg, int n, long measured_us, float pred_en_uwh);

int cost_model_correction_enabled(void);
const char *cost_source_name(CostSource src);

#endif // COST_MODEL_H
