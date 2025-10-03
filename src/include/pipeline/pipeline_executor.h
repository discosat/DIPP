#ifndef DIPP_PIPELINE_EXECUTOR_H
#define DIPP_PIPELINE_EXECUTOR_H

#include "image_batch.h"
#include "dipp_config.h"
#include "heuristics.h"

extern Heuristic *current_heuristic;

// Retrieve the pipeline assigned to the image batch and
// process the batch using this pipeline
int load_pipeline_and_execute(ImageBatch *input_batch);

// Return the total number of modules in the pipeline,
// These are distinct modules (multiple effort levels count as one).
int get_pipeline_length(int pipeline_id);

#endif // DIPP_PIPELINE_EXECUTOR_H