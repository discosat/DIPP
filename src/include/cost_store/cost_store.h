#ifndef COST_STORE_H
#define COST_STORE_H

#include <stdint.h>
#include <assert.h>
#include "image_batch.h"

/* Cost store, format 2 (Stage 5a).
 *
 * Format 1 was an exact-match table keyed by murmur3({num_images, batch_size,
 * pipeline}, seed = config hash): one latency/energy pair per distinct input
 * size, filled in on first execution, so every new tile count cold-missed to
 * the YAML hint and energy was never anything but that hint.
 *
 * Format 2 is keyed by the module CONFIG hash alone (murmur3 of the decoded
 * slot bytes, seed 42, dipp_config.c) and holds two kinds of entry per hash:
 *
 *   POINT (hash, n)  a value at one input size n. Seeded offline from the
 *                    board sweep (latency and energy both measured); created
 *                    on-board at an n with no point (latency measured, energy
 *                    = the model's prediction). Later on-board latency
 *                    observations fold in as an EMA (cost_model.c).
 *   MODEL (hash)     the fitted line: value at n_ref plus a slope per unit of
 *                    n. Used only for an n that has no point.
 *
 * Lookup order is point -> model -> YAML hint (cost_model.c). Keying by the
 * config hash means a store auto-invalidates for any slot whose YAML changed:
 * its entries are simply never found again and the hint takes over.
 *
 * The file image IS the seed format: tools/seed_cost_store.py writes a whole
 * CostStore (header + MAX_ENTRIES entries) and run.sh copies it to CACHE_FILE.
 * Both backends load CACHE_FILE at init when its header is valid, so a seeded
 * store works in MEM mode too. */

#define COST_STORE_MAGIC 0x32545343u /* "CST2" as a little-endian uint32 */
#define COST_STORE_VERSION 2u
#define MAX_ENTRIES 512
#define CACHE_FILE "/usr/share/dipp/cost.cache"

enum
{
    COST_KIND_POINT = 1,
    COST_KIND_MODEL = 2,
};

enum
{
    COST_ORIGIN_SEED = 1,    /* written by the seed tool */
    COST_ORIGIN_ONBOARD = 2, /* created or updated from an on-board measurement */
};

typedef enum COST_MODEL_LOOKUP_RESULT
{
    FOUND_CACHED = 0,     /* prediction came from the store (point or model) */
    NOT_FOUND = -1,       /* no implementation meets the requirements */
    FOUND_NOT_CACHED = -2 /* an implementation fits, but the YAML hint was used */
} COST_MODEL_LOOKUP_RESULT;

typedef struct CostEntry
{
    uint64_t timestamp;    /* LRU clock */
    uint32_t hash;         /* module config hash */
    uint8_t kind;          /* COST_KIND_* */
    uint8_t origin;        /* COST_ORIGIN_* */
    uint8_t valid;
    uint8_t _pad;
    int32_t n;             /* POINT: input size; MODEL: n_ref */
    uint32_t n_obs;        /* on-board latency observations folded in */
    uint32_t lat_us;       /* POINT: latency at n; MODEL: latency at n_ref */
    float lat_slope_us;    /* MODEL: microseconds per unit of n */
    float en_uwh;          /* POINT: energy at n; MODEL: energy at n_ref */
    float en_slope_uwh;    /* MODEL: microwatt-hours per unit of n */
    float _reserved[2];
} CostEntry;
static_assert(sizeof(CostEntry) == 48, "CostEntry is the on-disk/seed format: 48 bytes");

typedef struct CostStoreHeader
{
    uint32_t magic;
    uint32_t version;
    uint32_t n_entries; /* capacity of items[]; always MAX_ENTRIES */
    uint32_t _pad;
} CostStoreHeader;
static_assert(sizeof(CostStoreHeader) == 16, "CostStoreHeader is 16 bytes");

typedef struct CostStore
{
    CostStoreHeader hdr;
    CostEntry items[MAX_ENTRIES];
} CostStore;

typedef struct CostStoreImpl
{
    /* init sets *store to mapped or allocated memory, loaded from `filename`
       when that file carries a valid format-2 header. */
    int (*init)(CostStore **store, char *filename);
    /* Upsert: replaces the entry with the same (hash, kind, n), else takes a
       free slot, else evicts the LRU entry. The mmap backend msyncs. */
    int (*put)(CostStore *store, const CostEntry *entry);
    int (*clean_up)(CostStore *store);
} CostStoreImpl;

extern CostStore *cost_store;
extern CostStoreImpl *cost_store_impl;
extern CostStoreImpl cost_store_mmap;
extern CostStoreImpl cost_store_mem;
extern uint64_t global_time;

CostStoreImpl *get_cost_store_impl(StorageMode storage_type);

/* Shared helpers (cost_store.c). find_* touch the LRU clock of a hit. */
int cost_store_header_ok(const CostStore *store);
void cost_store_reset(CostStore *store);
int cost_store_find_point(CostStore *store, uint32_t hash, int32_t n);
int cost_store_find_model(CostStore *store, uint32_t hash);
int cost_store_upsert(CostStore *store, const CostEntry *entry);
/* Number of points and whether a model exists for a hash (for logging). */
void cost_store_count(const CostStore *store, uint32_t hash, int *points, int *model_idx);
int find_lru_index(CostStore *store);

#endif // COST_STORE_H
