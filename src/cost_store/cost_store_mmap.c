#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include "cost_store.h"
#include <stdio.h>

// mmap the file into memory
int cost_store_init_mmap(CostEntry *cache)
{

    size_t cache_size = MAX_ENTRIES * sizeof(CostEntry);

    int fd = open(CACHE_FILE, O_RDWR | O_CREAT, 0644);
    if (fd < 0)
    {
        printf("Error opening cache file: %s\n", CACHE_FILE);
        return -1;
    }

    // Ensure file is large enough
    if (ftruncate(fd, cache_size) == -1)
    {
        printf("Error resizing cache file: %s\n", CACHE_FILE);
        close(fd);
        return -1;
    }

    // Memory map the file
    cache = mmap(NULL, cache_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (cache == MAP_FAILED)
    {
        printf("Error mapping cache file: %s\n", CACHE_FILE);
        close(fd);
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

    close(fd);
    return 0;
}

CostStoreImpl cost_store_mmap = {
    .init = cost_store_init_mmap,
    .insert = cache_insert,
    .lookup = cache_lookup};