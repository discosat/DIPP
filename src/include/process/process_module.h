#ifndef DIPP_PROCESS_MODULE_H
#define DIPP_PROCESS_MODULE_H

#include "dipp_process.h"
#include "dipp_config.h"

extern int output_pipe[2]; // Pipe for inter-process result communication
extern int error_pipe[2];  // Pipe for inter-process error communication

int execute_module_in_process(ProcessFunction func, ImageBatch *input, ModuleParameterList *config)

#endif // DIPP_PROCESS_MODULE_H