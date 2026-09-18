#ifndef DIPP_PROCESS_MODULE_H
#define DIPP_PROCESS_MODULE_H

#include "image_batch.h"
#include "dipp_config.h"

// Pipeline run codes
typedef enum PIPELINE_PROCESS
{
    PROCESS_STOP = 0,
    PROCESS_ONE = 1,
    PROCESS_ALL = 2,
    PROCESS_WAIT_ONE = 3,
    PROCESS_WAIT_ALL = 4
} PIPELINE_PROCESS;

// Spawn a new process to isolate the module execution from the rest of the system.
// It sets up a timeout handler to kill the process if it exceeds the allowed time.
// On success (0) the module's result batch is copied into *result_out, read in
// full from the child's pipe; on failure (-1) the error has been recorded with
// set_error_param and *result_out is unspecified.
int execute_module_in_process(ProcessFunction func, ImageBatch *input,
                              ModuleParameterList *config, ImageBatch *result_out);

#endif // DIPP_PROCESS_MODULE_H