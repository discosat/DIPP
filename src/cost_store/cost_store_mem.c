#include <fcntl.h>
#include <unistd.h>
#include "cost_store.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

// Initialize in-memory CostStore: allocate outer CostStore if *store is NULL and zero items
int cost_store_init_mem(CostStore **store, char *filename)
{
    // filename unused for mem backend
    (void)filename;

    if (store == NULL)
    {
        printf("Error: provided CostStore** is NULL\n");
        return -1;
    }

    if (*store == NULL)
    {
        *store = (CostStore *)calloc(1, sizeof(CostStore));
        if (*store == NULL)
        {
            printf("Error allocating memory for CostStore\n");
            return -1;
        }
    }
    else
    {
        // zero items if caller provided an existing allocation
        memset((*store)->items, 0, MAX_ENTRIES * sizeof(CostEntry));
    }

    // Recalculate max timestamp to continue LRU order (should be 0 after memset but keep logic)
    for (int i = 0; i < MAX_ENTRIES; i++)
    {
        if ((*store)->items[i].valid && (*store)->items[i].timestamp > global_time)
        {
            global_time = (*store)->items[i].timestamp;
        }
    }

    return 0;
}

// mem-specific insert (same behavior as previous cache_insert)
void insert_mem(CostStore *store, uint32_t hash, uint32_t latency, float energy)
{
    global_time++;
    int idx = find_entry(store, hash);
    if (idx != -1)
    {
        store->items[idx].latency = latency;
        store->items[idx].energy = energy;
        store->items[idx].timestamp = global_time;
        // printf("Updated existing entry.\n");
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
            // printf("Inserted into free slot.\n");
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
        // printf("Evicted LRU and inserted.\n");
    }
}

// mem clean up: free allocated outer CostStore
int clean_up_mem(CostStore *store)
{
    if (store)
    {
        free(store);
    }
    return 0;
}

CostStoreImpl cost_store_mem = {
    .init = cost_store_init_mem,
    .insert = insert_mem,
    .lookup = cache_lookup,
    .clean_up = clean_up_mem};