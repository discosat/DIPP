#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <signal.h>
#include "process_module.h"
#include "image_batch.h"
#include "dipp_config.h"
#include "dipp_process_param.h"
#include "dipp_error.h"
#include "utils/minitrace.h"

int output_pipe[2] = {-1, -1};
int error_pipe[2] = {-1, -1};

// Signal handler for timeout
void timeout_handler(int signum)
{
    printf("Module timeout reached\n");
    uint16_t error_code = MODULE_EXIT_TIMEOUT;
    write(error_pipe[1], &error_code, sizeof(uint16_t));
    exit(EXIT_FAILURE); // Exit the child process with failure status
}

int execute_module_in_process(ProcessFunction func, ImageBatch *input, ModuleParameterList *config)
{
    MTR_BEGIN_FUNC();
    // Create a new process
    pid_t pid = fork();

    if (pid == 0)
    {
        // Set up signal handler for timeout and starm alarm timer
        signal(SIGALRM, timeout_handler);
        alarm(param_get_uint32(&module_timeout));

        // Child process: Execute the module function
        ImageBatch result = func(input, config, error_pipe);
        alarm(0); // stop timeout alarm
        size_t data_size = sizeof(result);
        write(output_pipe[1], &result, data_size); // Write the result to the pipe
        exit(EXIT_SUCCESS);
    }
    else
    {
        // Parent process: Wait for the child process to finish
        int status;
        waitpid(pid, &status, 0);

        if (WIFEXITED(status))
        {
            // Child process exited normally (EXIT_FAILURE)
            if (WEXITSTATUS(status) != 0)
            {
                uint16_t module_error;
                size_t res = read(error_pipe[0], &module_error, sizeof(uint16_t));
                if (res == -1)
                    set_error_param(PIPE_READ);
                else if (res == 0)
                    set_error_param(MODULE_EXIT_NORMAL);
                else if (module_error < 100)
                    set_error_param(MODULE_EXIT_CUSTOM + module_error);
                else
                    set_error_param(module_error);

                // invalidate cache, to be rebuilt in next pipeline invocation
                invalidate_cache();

                fprintf(stderr, "Child process exited with non-zero status\n");
                MTR_END_FUNC();
                return -1;
            }
        }
        else
        {
            // Child process did not exit normally (CRASH)
            set_error_param(MODULE_EXIT_CRASH);
            // invalidate cache
            invalidate_cache();
            fprintf(stderr, "Child process did not exit normally\n");
            MTR_END_FUNC();
            return -1;
        }

        MTR_END_FUNC();
        return 0;
    }
}