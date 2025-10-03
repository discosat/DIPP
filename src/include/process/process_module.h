#ifndef DIPP_PROCESS_MODULE_H
#define DIPP_PROCESS_MODULE_H

#include "image_batch.h"
#include "dipp_config.h"

extern int output_pipe[2]; // Pipe for inter-process result communication
extern int error_pipe[2];  // Pipe for inter-process error communication

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
int execute_module_in_process(ProcessFunction func, ImageBatch *input, ModuleParameterList *config);

#endif // DIPP_PROCESS_MODULE_H