/*
 * Regression test for the defect that made every archived trace invalid JSON.
 *
 * The daemon forks a child per module execution and the child exits via exit(),
 * which flushes the stdio buffers it INHERITED from the parent. Anything still
 * pending in the parent at fork time is therefore written again by every child.
 * With ~500 module executions per run, the trace header appeared ~501 times.
 *
 * Reproduces the shape of the real workload: init, fork N children that each
 * emit an event and exit(), then flush and shut down. Asserts the output has
 * exactly one header and parses as a single JSON object.
 *
 * Build natively; minitrace is portable.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include "utils/minitrace.h"

#define N_CHILDREN 200
#define TRACE_PATH "/tmp/dipp_trace_fork_test.json"

static int count_occurrences(const char *hay, const char *needle)
{
    int n = 0;
    for (const char *p = hay; (p = strstr(p, needle)); ++p)
        ++n;
    return n;
}

int main(void)
{
    unlink(TRACE_PATH);
    mtr_init(TRACE_PATH);

    MTR_BEGIN("test", "parent_work");
    for (int i = 0; i < N_CHILDREN; ++i)
    {
        fflush(NULL); /* the fix under test: drain before forking */
        pid_t pid = fork();
        if (pid == 0)
        {
            /* Child: behaves like execute_module_in_process -- does a little
               work and leaves via exit(), flushing inherited buffers. */
            MTR_INSTANT("test", "child_event");
            exit(0);
        }
        int status;
        waitpid(pid, &status, 0);
    }
    MTR_END("test", "parent_work");

    mtr_flush();
    mtr_shutdown();

    FILE *f = fopen(TRACE_PATH, "rb");
    if (!f) { fprintf(stderr, "FAIL: no trace file\n"); return 1; }
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc(len + 1);
    if (fread(buf, 1, len, f) != (size_t)len) { fprintf(stderr, "FAIL: short read\n"); return 1; }
    buf[len] = 0;
    fclose(f);

    int headers = count_occurrences(buf, "{\"traceEvents\":[");
    int opens   = count_occurrences(buf, "{\"traceEvents\"");
    unsigned long long dropped = (unsigned long long)mtr_dropped_events();

    printf("children forked : %d\n", N_CHILDREN);
    printf("trace bytes     : %ld\n", len);
    printf("headers found   : %d (expected 1)\n", headers);
    printf("dropped events  : %llu (expected 0)\n", dropped);

    int fail = 0;
    if (headers != 1) { fprintf(stderr, "FAIL: %d headers, expected exactly 1\n", headers); fail = 1; }
    if (opens != 1)   { fprintf(stderr, "FAIL: %d trace objects, expected 1\n", opens); fail = 1; }
    if (dropped != 0) { fprintf(stderr, "FAIL: %llu events dropped\n", dropped); fail = 1; }

    free(buf);
    if (!fail) printf("PASS\n");
    return fail;
}
