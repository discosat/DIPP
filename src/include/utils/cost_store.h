#ifndef COST_STORE_H
#define COST_STORE_H

#include <stdint.h>

#define MAX_ENTRIES 100
#define CACHE_FILE "/usr/share/dipp/cost.cache"

static uint64_t global_time = 0;

typedef struct
{
    uint32_t hash;
    uint16_t latency;
    uint16_t energy;
    uint64_t timestamp;
    uint8_t valid;
} CostEntry;

int cost_store_init(CostEntry *cache);
void cache_insert(CostEntry *cache, uint32_t hash, uint16_t latency, uint16_t energy);
int cache_lookup(CostEntry *cache, uint32_t hash, uint16_t *latency, uint16_t *energy);

#endif // COST_STORE_H