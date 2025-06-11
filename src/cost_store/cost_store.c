#include "cost_store.h"
#include <stdint.h>
#include <stdio.h>

uint64_t global_time = 0;

CostStoreImpl *get_cost_store_impl(StorageMode storage_type)
{

    // Initialize the cost cache based on the storage type
    switch (storage_type)
    {
    case STORAGE_MMAP:
        return &cost_store_mmap;
    case STORAGE_MEM:
        return &cost_store_mem;
    default:
        return NULL;
    }
}

// Find existing hash
int find_entry(CostEntry *cache, uint32_t hash)
{
    for (int i = 0; i < MAX_ENTRIES; ++i)
    {
        if (cache[i].valid && cache[i].hash == hash)
        {
            return i;
        }
    }
    return -1;
}

int find_lru_index(CostEntry *cache)
{
    uint64_t oldest = UINT64_MAX;
    int index = -1;
    for (int i = 0; i < MAX_ENTRIES; i++)
    {
        if (cache[i].valid && cache[i].timestamp < oldest)
        {
            oldest = cache[i].timestamp;
            index = i;
        }
    }
    return index;
}

// Insert
void cache_insert(CostEntry *cache, uint32_t hash, uint16_t latency, uint16_t energy)
{
    global_time++;
    // Check if entry already exists
    int idx = find_entry(cache, hash);
    // Update existing entry
    if (idx != -1)
    {
        cache[idx].latency = latency;
        cache[idx].energy = energy;
        cache[idx].timestamp = global_time;
        printf("Updated existing entry.\n");
        return;
    }

    // Check for free slot
    for (int i = 0; i < MAX_ENTRIES; i++)
    {
        if (!cache[i].valid)
        {
            cache[i].hash = hash;
            cache[i].latency = latency;
            cache[i].energy = energy;
            cache[i].valid = 1;
            cache[i].timestamp = global_time;
            printf("Inserted into free slot.\n");
            return;
        }
    }

    // Evict LRU entry and insert new entry
    int evict = find_lru_index(cache);
    if (evict != -1)
    {
        cache[evict].hash = hash;
        cache[evict].latency = latency;
        cache[evict].energy = energy;
        cache[evict].valid = 1;
        cache[evict].timestamp = global_time;
        printf("Evicted LRU and inserted.\n");
    }
}

int cache_lookup(CostEntry *cache, uint32_t hash, uint16_t *latency, uint16_t *energy)
{
    int idx = find_entry(cache, hash);
    if (idx != -1)
    {
        *latency = cache[idx].latency;
        *energy = cache[idx].energy;
        // Update timestamp for LRU
        cache[idx].timestamp = ++global_time;
        return idx;
    }
    return -1;
}