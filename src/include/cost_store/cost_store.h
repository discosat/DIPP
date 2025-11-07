#ifndef COST_STORE_H
#define COST_STORE_H

#include <stdint.h>
#include "image_batch.h"

#define MAX_ENTRIES 100
#define CACHE_FILE "/usr/share/dipp/cost.cache"

typedef enum COST_MODEL_LOOKUP_RESULT
{
    FOUND_CACHED = 0,     // got the optimal implementation from cost model
    NOT_FOUND = -1,       // could not find implementation that meets the requirements
    FOUND_NOT_CACHED = -2 // found an implementation that fulfils requirements, but not cached
} COST_MODEL_LOOKUP_RESULT;

typedef struct CostEntry
{
    uint32_t hash;
    uint16_t latency;
    uint16_t energy;
    uint64_t timestamp;
    uint8_t valid;
} CostEntry;

// New CostStore wrapper with statically allocated items (like PriorityQueue)
typedef struct CostStore
{
    CostEntry items[MAX_ENTRIES];
} CostStore;

typedef struct CostStoreImpl
{
    // init now takes CostStore ** so it can set the caller's pointer to mapped or allocated memory
    int (*init)(CostStore **store, char *filename);
    void (*insert)(CostStore *store, uint32_t hash, uint16_t latency, uint16_t energy);
    int (*lookup)(CostStore *store, uint32_t hash, uint16_t *latency, uint16_t *energy);
    int (*clean_up)(CostStore *store); // free/munmap backend resources
} CostStoreImpl;

// extern pointer to the malloc'd outer CostStore (items[] inside are static)
extern CostStore *cost_store;

CostStoreImpl *get_cost_store_impl(StorageMode storage_type);

// updated prototypes
int cache_lookup(CostStore *store, uint32_t hash, uint16_t *latency, uint16_t *energy);
void cache_insert(CostStore *store, uint32_t hash, uint16_t latency, uint16_t energy);

extern CostStoreImpl cost_store_mmap;
extern CostStoreImpl cost_store_mem;

extern CostStoreImpl *cost_store_impl;

extern uint64_t global_time;

#endif // COST_STORE_H