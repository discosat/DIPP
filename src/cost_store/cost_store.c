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

// Find existing hash in the CostStore's statically allocated items
int find_entry(CostStore *store, uint32_t hash)
{
    for (int i = 0; i < MAX_ENTRIES; ++i)
    {
        if (store->items[i].valid && store->items[i].hash == hash)
        {
            return i;
        }
    }
    return -1;
}

int find_lru_index(CostStore *store)
{
    uint64_t oldest = UINT64_MAX;
    int index = -1;
    for (int i = 0; i < MAX_ENTRIES; i++)
    {
        if (store->items[i].valid && store->items[i].timestamp < oldest)
        {
            oldest = store->items[i].timestamp;
            index = i;
        }
    }
    return index;
}

// lookup remains shared
int cache_lookup(CostStore *store, uint32_t hash, uint32_t *latency, float *energy)
{
    int idx = find_entry(store, hash);
    if (idx != -1)
    {
        *latency = store->items[idx].latency; // now uint32_t (microseconds)
        *energy = store->items[idx].energy;   // now float
        // Update timestamp for LRU
        store->items[idx].timestamp = ++global_time;
        return idx;
    }
    return -1;
}