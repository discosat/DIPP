#include "utils/timestamp.h"

#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <string.h>

void log_timestamp(const char *message)
{
    // get timestamp in microseconds
    struct timespec tv;
    clock_gettime(CLOCK_MONOTONIC, &tv);
    long long microseconds = (long long)tv.tv_sec * 1000000 + tv.tv_nsec / 1000;

    printf("%s: %lld\n", message, microseconds);
}