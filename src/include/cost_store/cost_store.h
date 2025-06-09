#ifndef COST_STORE_H
#define COST_STORE_H

#include <stdint.h>
#include "dipp_process.h"

#define MAX_ENTRIES 100
#define CACHE_FILE "/usr/share/dipp/cost.cache"

uint64_t global_time = 0;

typedef struct CostEntry
{
    uint32_t hash;
    uint16_t latency;
    uint16_t energy;
    uint64_t timestamp;
    uint8_t valid;
} CostEntry;

typedef struct CostStoreImpl
{
    int (*init)(CostEntry *cache);
    void (*insert)(CostEntry *cache, uint32_t hash, uint16_t latency, uint16_t energy);
    int (*lookup)(CostEntry *cache, uint32_t hash, uint16_t *latency, uint16_t *energy);
} CostStoreImpl;

extern CostEntry *cost_cache;

CostStoreImpl *get_cost_store_impl(StorageMode storage_type);

extern CostStoreImpl cost_store_mmap;
extern CostStoreImpl cost_store_mem;

#endif // COST_STORE_H