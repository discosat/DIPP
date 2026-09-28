/*
 * Cost model / cost store v2 tests.
 *
 * What the scheduler decides on is cost_model_predict: a measured POINT at
 * exactly this input size, else the fitted MODEL line, else the YAML hint.
 * v1 was an exact-match table whose energy was always the hint, so the
 * lookup order, the seed load and the on-board latency correction are all
 * new behaviour and all of it is testable without a board: the store is
 * plain POSIX and cost_model.c touches nothing else.
 *
 * Build natively:
 *   gcc -o /tmp/test_cost_model tests/test_cost_model.c \
 *       src/cost_store/cost_model.c src/cost_store/cost_store.c \
 *       src/cost_store/cost_store_mem.c src/cost_store/cost_store_mmap.c \
 *       -Isrc/include -Isrc/include/cost_store -Isrc/include/image
 *   /tmp/test_cost_model
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>

#include "cost_store.h"
#include "cost_model.h"

/* Owned by dipp_process.c in the daemon; the test is the daemon here. */
CostStore *cost_store = NULL;
CostStoreImpl *cost_store_impl = NULL;

#define SEED_FILE "/tmp/sipp_cost_test.cache"
#define HASH_A 0xAAAA0001u
#define HASH_B 0xBBBB0002u

static int failures = 0;
static void check(const char *what, int ok)
{
    printf("  %-58s %s\n", what, ok ? "PASS" : "FAIL");
    if (!ok)
        failures++;
}
static int close_to(double a, double b, double tol) { return (a - b < tol) && (b - a < tol); }

static ModuleParameterList mkcfg(uint32_t hash, uint32_t lat_hint, uint32_t en_hint)
{
    ModuleParameterList c;
    memset(&c, 0, sizeof(c));
    c.hash = hash;
    c.latency_cost = lat_hint;
    c.energy_cost = en_hint;
    return c;
}

static CostEntry mkpoint(uint32_t hash, int32_t n, uint32_t lat_us, float en_uwh, uint8_t origin)
{
    CostEntry e;
    memset(&e, 0, sizeof(e));
    e.hash = hash;
    e.kind = COST_KIND_POINT;
    e.origin = origin;
    e.valid = 1;
    e.n = n;
    e.lat_us = lat_us;
    e.en_uwh = en_uwh;
    return e;
}

static CostEntry mkmodel(uint32_t hash, int32_t n_ref, uint32_t lat_ref, float lat_slope,
                         float en_ref, float en_slope)
{
    CostEntry e;
    memset(&e, 0, sizeof(e));
    e.hash = hash;
    e.kind = COST_KIND_MODEL;
    e.origin = COST_ORIGIN_SEED;
    e.valid = 1;
    e.n = n_ref;
    e.lat_us = lat_ref;
    e.lat_slope_us = lat_slope;
    e.en_uwh = en_ref;
    e.en_slope_uwh = en_slope;
    return e;
}

/* A store in memory, no file behind it. */
static void fresh_store(void)
{
    if (cost_store)
        free(cost_store);
    cost_store = calloc(1, sizeof(CostStore));
    cost_store_reset(cost_store);
    cost_store_impl = &cost_store_mem;
}

/* ------------------------------------------------------------------ cases */

static void t_hint_when_empty(void)
{
    fresh_store();
    ModuleParameterList cfg = mkcfg(HASH_A, 1234567, 42);
    uint32_t lat;
    float en;
    CostSource src = cost_model_predict(&cfg, 99, &lat, &en);
    check("empty store -> hint", src == COST_SRC_HINT && lat == 1234567 && close_to(en, 42.0, 1e-6));
    check("cost_source_name(hint)", strcmp(cost_source_name(src), "hint") == 0);


}

static void t_model_line(void)
{
    fresh_store();
    /* 1.0 s at 99 tiles, +1 ms per tile; 5 uWh at 99, +0.05 uWh per tile. */
    CostEntry m = mkmodel(HASH_A, 99, 1000000, 1000.0f, 5.0f, 0.05f);
    cost_store_upsert(cost_store, &m);

    ModuleParameterList cfg = mkcfg(HASH_A, 7, 7);
    uint32_t lat;
    float en;
    CostSource src = cost_model_predict(&cfg, 199, &lat, &en);
    check("model at n above n_ref",
          src == COST_SRC_MODEL && lat == 1100000 && close_to(en, 10.0, 1e-4));

    src = cost_model_predict(&cfg, 99, &lat, &en);
    check("model at n_ref returns the reference value",
          src == COST_SRC_MODEL && lat == 1000000 && close_to(en, 5.0, 1e-4));

    /* A line extrapolated far below n_ref must not go negative: the old
       int32-printed-as-%u failure mode is exactly this, seen as ~4.29e9. */
    CostEntry steep = mkmodel(HASH_B, 99, 1000000, 100000.0f, 5.0f, 1.0f);
    cost_store_upsert(cost_store, &steep);
    ModuleParameterList cfg_b = mkcfg(HASH_B, 7, 7);
    src = cost_model_predict(&cfg_b, 1, &lat, &en);
    check("negative extrapolation clamps to zero, not to 4.29e9",
          src == COST_SRC_MODEL && lat == 0 && close_to(en, 0.0, 1e-6));

    /* Another slot's model must not answer for this one. */
    ModuleParameterList other = mkcfg(0xDEADBEEFu, 4242, 9);
    src = cost_model_predict(&other, 199, &lat, &en);
    check("a different config hash falls through to its own hint",
          src == COST_SRC_HINT && lat == 4242);
}

static void t_point_beats_model(void)
{
    fresh_store();
    CostEntry m = mkmodel(HASH_A, 99, 1000000, 1000.0f, 5.0f, 0.05f);
    CostEntry p = mkpoint(HASH_A, 199, 2000000, 77.0f, COST_ORIGIN_SEED);
    cost_store_upsert(cost_store, &m);
    cost_store_upsert(cost_store, &p);

    ModuleParameterList cfg = mkcfg(HASH_A, 7, 7);
    uint32_t lat;
    float en;
    CostSource src = cost_model_predict(&cfg, 199, &lat, &en);
    check("measured point wins over the fitted line at its own n",
          src == COST_SRC_POINT && lat == 2000000 && close_to(en, 77.0, 1e-4));

    src = cost_model_predict(&cfg, 200, &lat, &en);
    check("one n off the point falls back to the line",
          src == COST_SRC_MODEL && lat == 1101000);
}

static void t_observe_creates_point(void)
{
    fresh_store();
    CostEntry m = mkmodel(HASH_A, 99, 1000000, 1000.0f, 5.0f, 0.05f);
    cost_store_upsert(cost_store, &m);
    ModuleParameterList cfg = mkcfg(HASH_A, 7, 7);

    uint32_t lat;
    float en;
    cost_model_predict(&cfg, 150, &lat, &en); /* the prediction that was debited */
    cost_model_observe_latency(&cfg, 150, 1234567, en);

    int idx = cost_store_find_point(cost_store, HASH_A, 150);
    check("observing at an unmeasured n creates a point", idx != -1);
    if (idx == -1)
        return;
    const CostEntry *e = &cost_store->items[idx];
    check("new point carries the measured latency", e->lat_us == 1234567);
    check("new point's energy is the prediction, never a measurement",
          close_to(e->en_uwh, en, 1e-4));
    check("new point is marked on-board", e->origin == COST_ORIGIN_ONBOARD && e->n_obs == 1);

    CostSource src = cost_model_predict(&cfg, 150, &lat, &en);
    check("the new point is what the next prediction uses",
          src == COST_SRC_POINT && lat == 1234567);
}

static void t_observe_ema(void)
{
    fresh_store();
    CostEntry p = mkpoint(HASH_A, 99, 1000000, 5.0f, COST_ORIGIN_SEED);
    cost_store_upsert(cost_store, &p);
    ModuleParameterList cfg = mkcfg(HASH_A, 7, 7);

    /* The first execution after boot carries the VX graph compile; alpha 0.3
       is what stops one such outlier from owning this n for the whole run. */
    cost_model_observe_latency(&cfg, 99, 2000000, 5.0f);
    int idx = cost_store_find_point(cost_store, HASH_A, 99);
    check("EMA alpha 0.3 on an existing point",
          idx != -1 && cost_store->items[idx].lat_us == 1300000);
    check("seeded energy survives a latency observation",
          idx != -1 && close_to(cost_store->items[idx].en_uwh, 5.0, 1e-4));
    check("a seeded point that took an observation is re-marked on-board",
          idx != -1 && cost_store->items[idx].origin == COST_ORIGIN_ONBOARD &&
              cost_store->items[idx].n_obs == 1);

    cost_model_observe_latency(&cfg, 99, 2000000, 5.0f);
    idx = cost_store_find_point(cost_store, HASH_A, 99);
    check("a second observation converges further (1.51 s)",
          idx != -1 && cost_store->items[idx].lat_us == 1510000 &&
              cost_store->items[idx].n_obs == 2);

    cost_model_observe_latency(&cfg, 99, -1, 5.0f);
    idx = cost_store_find_point(cost_store, HASH_A, 99);
    check("a negative measurement is ignored",
          idx != -1 && cost_store->items[idx].lat_us == 1510000);
}

static void t_seed_file_roundtrip(void)
{
    /* This is the seed format: seed_cost_store.py writes exactly this image
       and run.sh copies it to CACHE_FILE. */
    CostStore *img = calloc(1, sizeof(CostStore));
    cost_store_reset(img);
    CostEntry m = mkmodel(HASH_A, 99, 1000000, 1000.0f, 5.0f, 0.05f);
    CostEntry p = mkpoint(HASH_A, 99, 999999, 4.5f, COST_ORIGIN_SEED);
    cost_store_upsert(img, &m);
    cost_store_upsert(img, &p);
    FILE *f = fopen(SEED_FILE, "wb");
    size_t wrote = fwrite(img, 1, sizeof(CostStore), f);
    fclose(f);
    free(img);
    check("seed image is 24,592 bytes", wrote == sizeof(CostStore) && sizeof(CostStore) == 24592);

    cost_store = NULL;
    cost_store_impl = &cost_store_mem;
    cost_store_impl->init(&cost_store, SEED_FILE);
    ModuleParameterList cfg = mkcfg(HASH_A, 7, 7);
    uint32_t lat;
    float en;
    CostSource src = cost_model_predict(&cfg, 99, &lat, &en);
    check("MEM backend starts from the seed file",
          src == COST_SRC_POINT && lat == 999999 && close_to(en, 4.5, 1e-4));
    src = cost_model_predict(&cfg, 150, &lat, &en);
    check("the seeded model is loaded too", src == COST_SRC_MODEL && lat == 1051000);
    cost_store_impl->clean_up(cost_store);
    cost_store = NULL;

    /* Format 1, or any other rubbish, must reset rather than be believed. */
    f = fopen(SEED_FILE, "wb");
    CostStore *bad = calloc(1, sizeof(CostStore));
    bad->hdr.magic = 0x31545343u; /* "CST1" */
    fwrite(bad, 1, sizeof(CostStore), f);
    fclose(f);
    free(bad);
    cost_store_impl->init(&cost_store, SEED_FILE);
    src = cost_model_predict(&cfg, 99, &lat, &en);
    check("a format-1 file is reset, not trusted", src == COST_SRC_HINT && lat == 7);
    cost_store_impl->clean_up(cost_store);
    cost_store = NULL;
    unlink(SEED_FILE);
}

static void t_mmap_persists(void)
{
    unlink(SEED_FILE);
    cost_store = NULL;
    cost_store_impl = &cost_store_mmap;
    cost_store_impl->init(&cost_store, SEED_FILE);
    ModuleParameterList cfg = mkcfg(HASH_A, 7, 7);
    cost_model_observe_latency(&cfg, 42, 555000, 3.0f);
    cost_store_impl->clean_up(cost_store);

    cost_store = NULL;
    cost_store_impl->init(&cost_store, SEED_FILE);
    uint32_t lat;
    float en;
    CostSource src = cost_model_predict(&cfg, 42, &lat, &en);
    check("MMAP backend: an on-board point survives a restart",
          src == COST_SRC_POINT && lat == 555000);
    cost_store_impl->clean_up(cost_store);
    cost_store = NULL;
    unlink(SEED_FILE);
}

static void t_lru_prefers_onboard(void)
{
    fresh_store();
    /* Fill with seeded points, then one on-board point, then overflow. */
    for (int i = 0; i < MAX_ENTRIES - 1; i++)
    {
        CostEntry e = mkpoint(HASH_A, i, 1000, 1.0f, COST_ORIGIN_SEED);
        cost_store_upsert(cost_store, &e);
    }
    CostEntry onboard = mkpoint(HASH_B, 7, 2000, 2.0f, COST_ORIGIN_ONBOARD);
    cost_store_upsert(cost_store, &onboard);
    /* Touch every seeded entry so the on-board one is not the oldest either. */
    for (int i = 0; i < MAX_ENTRIES - 1; i++)
        cost_store_find_point(cost_store, HASH_A, i);

    CostEntry fresh = mkpoint(0xC0FFEEu, 1, 3000, 3.0f, COST_ORIGIN_ONBOARD);
    cost_store_upsert(cost_store, &fresh);
    check("eviction takes the on-board entry, not a seeded one",
          cost_store_find_point(cost_store, HASH_B, 7) == -1 &&
              cost_store_find_point(cost_store, HASH_A, 0) != -1 &&
              cost_store_find_point(cost_store, 0xC0FFEEu, 1) != -1);

    int points, midx;
    cost_store_count(cost_store, HASH_A, &points, &midx);
    check("cost_store_count reports points and no model",
          points == MAX_ENTRIES - 1 && midx == -1);
}

/* DIPP_LATENCY_CORRECTION is read once and cached, so the frozen-store case
   runs as a child process with the variable already set. */
static int run_no_correction_child(void)
{
    fresh_store();
    CostEntry p = mkpoint(HASH_A, 99, 1000000, 5.0f, COST_ORIGIN_SEED);
    cost_store_upsert(cost_store, &p);
    ModuleParameterList cfg = mkcfg(HASH_A, 7, 7);
    cost_model_observe_latency(&cfg, 99, 9000000, 5.0f);
    cost_model_observe_latency(&cfg, 123, 9000000, 5.0f);
    int idx = cost_store_find_point(cost_store, HASH_A, 99);
    int unchanged = (idx != -1 && cost_store->items[idx].lat_us == 1000000);
    int no_new = (cost_store_find_point(cost_store, HASH_A, 123) == -1);
    return (unchanged && no_new && !cost_model_correction_enabled()) ? 0 : 1;
}

static void t_no_correction(const char *self)
{
    pid_t pid = fork();
    if (pid == 0)
    {
        setenv("DIPP_LATENCY_CORRECTION", "0", 1);
        execl(self, self, "nocorr", (char *)NULL);
        _exit(127);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    check("DIPP_LATENCY_CORRECTION=0 freezes the store",
          WIFEXITED(status) && WEXITSTATUS(status) == 0);
}

int main(int argc, char *argv[])
{
    if (argc > 1 && strcmp(argv[1], "nocorr") == 0)
        return run_no_correction_child();

    printf("cost model / cost store v2\n");
    t_hint_when_empty();
    t_model_line();
    t_point_beats_model();
    t_observe_creates_point();
    t_observe_ema();
    t_seed_file_roundtrip();
    t_mmap_persists();
    t_lru_prefers_onboard();
    t_no_correction(argv[0]);

    printf("%s (%d failures)\n", failures ? "FAILED" : "OK", failures);
    return failures ? 1 : 0;
}
