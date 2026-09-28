#include <fcntl.h>
#include <unistd.h>
#include "cost_store.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>

static void adopt_lru_clock(CostStore *store)
{
    for (int i = 0; i < MAX_ENTRIES; i++)
        if (store->items[i].valid && store->items[i].timestamp > global_time)
            global_time = store->items[i].timestamp;
}

/* In-memory store. Nothing it learns survives the process, but it still
   STARTS from the seed file when one is present: a MEM-mode run must decide
   on the same offline characterisation as a HYBRID one, or the storage modes
   are not comparable. */
int cost_store_init_mem(CostStore **store, char *filename)
{
    const char *file = filename ? filename : CACHE_FILE;

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

    int loaded = 0;
    FILE *f = fopen(file, "rb");
    if (f)
    {
        size_t got = fread(*store, 1, sizeof(CostStore), f);
        fclose(f);
        if (got == sizeof(CostStore) && cost_store_header_ok(*store))
            loaded = 1;
        else
            printf("COST_STORE_RESET file=%s reason=%s\n", file,
                   got != sizeof(CostStore) ? "short_file" : "bad_header");
    }
    if (!loaded)
        cost_store_reset(*store);

    int n = 0;
    for (int i = 0; i < MAX_ENTRIES; i++)
        n += (*store)->items[i].valid ? 1 : 0;
    printf("COST_STORE_INIT backend=mem file=%s loaded=%d entries=%d\n", file, loaded, n);

    adopt_lru_clock(*store);
    return 0;
}

int put_mem(CostStore *store, const CostEntry *entry)
{
    return cost_store_upsert(store, entry);
}

int clean_up_mem(CostStore *store)
{
    if (store)
        free(store);
    return 0;
}

CostStoreImpl cost_store_mem = {
    .init = cost_store_init_mem,
    .put = put_mem,
    .clean_up = clean_up_mem};
