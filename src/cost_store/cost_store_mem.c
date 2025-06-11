#include <fcntl.h>
#include <unistd.h>
#include "cost_store.h"
#include <stdio.h>

// mmap the file into memory
int cost_store_init_mem(CostEntry *cache)
{

    cache = (CostEntry *)calloc(MAX_ENTRIES, sizeof(CostEntry));
    if (cache == NULL)
    {
        printf("Error allocating memory for cache\n");
        return -1;
    }

    // Recalculate max timestamp to continue LRU order
    for (int i = 0; i < MAX_ENTRIES; i++)
    {
        if (cache[i].valid && cache[i].timestamp > global_time)
        {
            global_time = cache[i].timestamp;
        }
    }

    return 0;
}

CostStoreImpl cost_store_mem = {
    .init = cost_store_init_mem,
    .insert = cache_insert,
    .lookup = cache_lookup};