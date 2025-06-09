#ifndef DIPP_PIPELINE_EXECUTOR_H
#define DIPP_PIPELINE_EXECUTOR_H

#include "dipp_process.h"
#include "dipp_config.h"
#include "heuristics.h"

extern Heuristic *current_heuristic;

int load_pipeline_and_execute(ImageBatch *input_batch);
int get_pipeline_length(int pipeline_id);

#endif // DIPP_PIPELINE_EXECUTOR_H