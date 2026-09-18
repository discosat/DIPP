#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
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

/* Pipes for the CURRENT module execution only. They used to be created once
   per pipeline and both ends stayed open in the parent for the whole run, so
   a child that died without writing an error code left read() blocked on a
   pipe whose write end the parent itself still held -- forever. Now each
   execution gets fresh pipes, the parent closes its write ends right after
   the fork, and a child that writes nothing yields EOF. */
static int output_pipe[2] = {-1, -1};
static int error_pipe[2] = {-1, -1};

/* A module that hangs must be killed, or the daemon waits on it forever.
   module_timeout is a CSP parameter and reads 0 on a fresh image, and
   alarm(0) CANCELS rather than schedules -- which is why MODULE_EXIT_TIMEOUT
   had never fired. So 0 means "the default", not "no timeout". The default
   must exceed the slowest legitimate execution: JPEG XL effort 5 takes
   20.3 s on a 99-tile frame (analysis/stage4.5-models), so 30 s. */
#define DEFAULT_MODULE_TIMEOUT_S 30

// Signal handler for timeout
void timeout_handler(int signum)
{
    (void)signum;
    uint16_t error_code = MODULE_EXIT_TIMEOUT;
    if (write(error_pipe[1], &error_code, sizeof(uint16_t)) < 0)
    {
        /* nothing to do: the parent will see EOF and report MODULE_EXIT_NORMAL */
    }
    _exit(EXIT_FAILURE); // Exit the child process with failure status
}

static void close_pair(int p[2])
{
    for (int k = 0; k < 2; k++)
    {
        if (p[k] != -1)
        {
            close(p[k]);
            p[k] = -1;
        }
    }
}

/* Read exactly n bytes or until EOF/error. Returns bytes read. A pipe read
   is not guaranteed to deliver a whole struct in one call, and the old
   single read() accepted a short result as a full ImageBatch. */
static ssize_t read_full(int fd, void *buf, size_t n)
{
    size_t got = 0;
    while (got < n)
    {
        ssize_t r = read(fd, (char *)buf + got, n - got);
        if (r < 0)
        {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (r == 0)
            break;
        got += (size_t)r;
    }
    return (ssize_t)got;
}

int execute_module_in_process(ProcessFunction func, ImageBatch *input,
                              ModuleParameterList *config, ImageBatch *result_out)
{
    MTR_BEGIN_FUNC();

    if (pipe(output_pipe) == -1 || pipe(error_pipe) == -1)
    {
        close_pair(output_pipe);
        close_pair(error_pipe);
        set_error_param(PIPE_CREATE);
        MTR_END_FUNC();
        return -1;
    }

    /* Drain every stdio buffer before forking. The child exits via exit(),
       which flushes the buffers it inherited, so anything still pending in the
       parent at fork time is written out again by each of the ~500 children.
       That is how the trace file acquired ~501 copies of its header and stopped
       being valid JSON. Flushing here keeps the child's own output intact,
       which _exit() in the child would discard. */
    fflush(NULL);

    // Create a new process
    pid_t pid = fork();
    if (pid == -1)
    {
        close_pair(output_pipe);
        close_pair(error_pipe);
        set_error_param(MODULE_EXIT_CRASH);
        MTR_END_FUNC();
        return -1;
    }

    if (pid == 0)
    {
        /* Child: the read ends are the parent's. */
        close(output_pipe[0]);
        close(error_pipe[0]);

        uint32_t timeout_s = param_get_uint32(&module_timeout);
        if (timeout_s == 0)
            timeout_s = DEFAULT_MODULE_TIMEOUT_S;
        signal(SIGALRM, timeout_handler);
        alarm(timeout_s);

        // Child process: Execute the module function
        ImageBatch result = func(input, config, error_pipe);
        alarm(0); // stop timeout alarm
        size_t data_size = sizeof(result);
        if (write(output_pipe[1], &result, data_size) != (ssize_t)data_size)
            exit(EXIT_FAILURE);
        exit(EXIT_SUCCESS);
    }

    /* Parent: give up the write ends so a silent child produces EOF. */
    close(output_pipe[1]);
    output_pipe[1] = -1;
    close(error_pipe[1]);
    error_pipe[1] = -1;

    int status;
    while (waitpid(pid, &status, 0) == -1 && errno == EINTR)
        ;

    int rc = 0;
    if (WIFEXITED(status) && WEXITSTATUS(status) == 0)
    {
        ssize_t got = read_full(output_pipe[0], result_out, sizeof(*result_out));
        if (got == -1)
        {
            set_error_param(PIPE_READ);
            rc = -1;
        }
        else if (got != (ssize_t)sizeof(*result_out))
        {
            /* Short or empty result: the module exited 0 without a whole
               ImageBatch. Treat it as a module failure, not as data. */
            printf("Module result is %zd bytes, expected %zu\n", got, sizeof(*result_out));
            set_error_param(PIPE_EMPTY);
            rc = -1;
        }
    }
    else if (WIFEXITED(status))
    {
        // Child process exited normally with EXIT_FAILURE
        uint16_t module_error;
        ssize_t res = read_full(error_pipe[0], &module_error, sizeof(uint16_t));
        if (res == -1)
            set_error_param(PIPE_READ);
        else if (res == 0)
            set_error_param(MODULE_EXIT_NORMAL); /* exited non-zero, said nothing */
        else if (module_error < 100)
            set_error_param(MODULE_EXIT_CUSTOM + module_error);
        else
            set_error_param(module_error);

        fprintf(stderr, "Child process exited with non-zero status %d\n", WEXITSTATUS(status));
        rc = -1;
    }
    else
    {
        // Child process did not exit normally (signal: crash, or a kill)
        set_error_param(MODULE_EXIT_CRASH);
        fprintf(stderr, "Child process did not exit normally (signal %d)\n",
                WIFSIGNALED(status) ? WTERMSIG(status) : 0);
        rc = -1;
    }

    if (rc == -1)
    {
        // invalidate cache, to be rebuilt in next pipeline invocation
        invalidate_cache();
    }

    close_pair(output_pipe);
    close_pair(error_pipe);
    MTR_END_FUNC();
    return rc;
}
