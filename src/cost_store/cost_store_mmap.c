#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include "cost_store.h"
#include <stdio.h>
#include <string.h>
#include <errno.h>

// mmap the file into memory and point the caller's pointer to the mapped region.
// If file doesn't exist, create it, ftruncate to size and zero the contents before using.
int cost_store_init_mmap(CostStore **store, char *filename)
{
    const char *file = filename ? filename : CACHE_FILE;
    size_t cache_size = sizeof(CostStore);
    struct stat st;

    if (store == NULL)
    {
        printf("Error: provided CostStore** is NULL\n");
        return -1;
    }

    // Check if file exists
    int exists = (stat(file, &st) == 0);

    int fd = open(file, O_RDWR | O_CREAT, 0644);
    if (fd < 0)
    {
        printf("Error opening/creating cache file: %s (%s)\n", file, strerror(errno));
        return -1;
    }

    // Ensure file is large enough
    if (ftruncate(fd, (off_t)cache_size) == -1)
    {
        printf("Error resizing cache file: %s (%s)\n", file, strerror(errno));
        close(fd);
        return -1;
    }

    // Memory map the file (map whole CostStore). Keep the mapping live — we will work on it directly.
    void *mapped = mmap(NULL, cache_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (mapped == MAP_FAILED)
    {
        printf("Error mapping cache file: %s (%s)\n", file, strerror(errno));
        close(fd);
        return -1;
    }

    // If the file was newly created (didn't exist), zero the contents so the file holds zeros
    if (!exists)
    {
        memset(mapped, 0, cache_size);
        // persist zeros
        msync(mapped, cache_size, MS_SYNC);
    }

    // Set the caller's pointer to the mapped (persisted) memory; operate on it directly thereafter.
    *store = (CostStore *)mapped;

    // Recalculate max timestamp to continue LRU order
    for (int i = 0; i < MAX_ENTRIES; i++)
    {
        if ((*store)->items[i].valid && (*store)->items[i].timestamp > global_time)
        {
            global_time = (*store)->items[i].timestamp;
        }
    }

    // Keep mapping alive; close FD (mapping remains valid)
    close(fd);
    return 0;
}

// mmap-specific insert: same as mem but persist to disk
void insert_mmap(CostStore *store, uint32_t hash, uint32_t latency, float energy)
{
    global_time++;
    int idx = find_entry(store, hash);
    if (idx != -1)
    {
        store->items[idx].latency = latency;
        store->items[idx].energy = energy;
        store->items[idx].timestamp = global_time;
        // persist change
        msync(store, sizeof(CostStore), MS_SYNC);
        // printf("Updated existing entry (mmap, synced).\n");
        return;
    }

    for (int i = 0; i < MAX_ENTRIES; i++)
    {
        if (!store->items[i].valid)
        {
            store->items[i].hash = hash;
            store->items[i].latency = latency;
            store->items[i].energy = energy;
            store->items[i].valid = 1;
            store->items[i].timestamp = global_time;
            msync(store, sizeof(CostStore), MS_SYNC);
            // printf("Inserted into free slot (mmap, synced).\n");
            return;
        }
    }

    int evict = find_lru_index(store);
    if (evict != -1)
    {
        store->items[evict].hash = hash;
        store->items[evict].latency = latency;
        store->items[evict].energy = energy;
        store->items[evict].valid = 1;
        store->items[evict].timestamp = global_time;
        msync(store, sizeof(CostStore), MS_SYNC);
        // printf("Evicted LRU and inserted (mmap, synced).\n");
    }
}

// mmap clean up: unmap the mapped CostStore
int clean_up_mmap(CostStore *store)
{
    if (store)
    {
        munmap(store, sizeof(CostStore));
    }
    return 0;
}

CostStoreImpl cost_store_mmap = {
    .init = cost_store_init_mmap,
    .insert = insert_mmap,
    .lookup = cache_lookup,
    .clean_up = clean_up_mmap};