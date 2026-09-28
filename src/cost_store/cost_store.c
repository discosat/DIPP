#include "cost_store.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>

uint64_t global_time = 0;

CostStoreImpl *get_cost_store_impl(StorageMode storage_type)
{
    switch (storage_type)
    {
    case STORAGE_HYBRID:
    case STORAGE_MMAP:
        return &cost_store_mmap;
    case STORAGE_MEM:
        return &cost_store_mem;
    default:
        return NULL;
    }
}

int cost_store_header_ok(const CostStore *store)
{
    return store->hdr.magic == COST_STORE_MAGIC &&
           store->hdr.version == COST_STORE_VERSION &&
           store->hdr.n_entries == MAX_ENTRIES;
}

void cost_store_reset(CostStore *store)
{
    memset(store, 0, sizeof(*store));
    store->hdr.magic = COST_STORE_MAGIC;
    store->hdr.version = COST_STORE_VERSION;
    store->hdr.n_entries = MAX_ENTRIES;
}

int cost_store_find_point(CostStore *store, uint32_t hash, int32_t n)
{
    for (int i = 0; i < MAX_ENTRIES; ++i)
    {
        CostEntry *e = &store->items[i];
        if (e->valid && e->kind == COST_KIND_POINT && e->hash == hash && e->n == n)
        {
            e->timestamp = ++global_time;
            return i;
        }
    }
    return -1;
}

int cost_store_find_model(CostStore *store, uint32_t hash)
{
    for (int i = 0; i < MAX_ENTRIES; ++i)
    {
        CostEntry *e = &store->items[i];
        if (e->valid && e->kind == COST_KIND_MODEL && e->hash == hash)
        {
            e->timestamp = ++global_time;
            return i;
        }
    }
    return -1;
}

void cost_store_count(const CostStore *store, uint32_t hash, int *points, int *model_idx)
{
    *points = 0;
    *model_idx = -1;
    for (int i = 0; i < MAX_ENTRIES; ++i)
    {
        const CostEntry *e = &store->items[i];
        if (!e->valid || e->hash != hash)
            continue;
        if (e->kind == COST_KIND_POINT)
            (*points)++;
        else if (e->kind == COST_KIND_MODEL)
            *model_idx = i;
    }
}

int find_lru_index(CostStore *store)
{
    uint64_t oldest = UINT64_MAX;
    int index = -1;
    for (int i = 0; i < MAX_ENTRIES; i++)
    {
        /* Seeded entries are the offline characterisation; evict on-board
           points first, and a seeded one only when nothing else is left. */
        if (store->items[i].valid && store->items[i].origin == COST_ORIGIN_ONBOARD &&
            store->items[i].timestamp < oldest)
        {
            oldest = store->items[i].timestamp;
            index = i;
        }
    }
    if (index != -1)
        return index;
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

int cost_store_upsert(CostStore *store, const CostEntry *entry)
{
    int idx = (entry->kind == COST_KIND_MODEL)
                  ? cost_store_find_model(store, entry->hash)
                  : cost_store_find_point(store, entry->hash, entry->n);
    if (idx == -1)
    {
        for (int i = 0; i < MAX_ENTRIES; i++)
        {
            if (!store->items[i].valid)
            {
                idx = i;
                break;
            }
        }
    }
    if (idx == -1)
        idx = find_lru_index(store);
    if (idx == -1)
        return -1;
    store->items[idx] = *entry;
    store->items[idx].valid = 1;
    store->items[idx].timestamp = ++global_time;
    return idx;
}
