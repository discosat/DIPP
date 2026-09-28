#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cost_model.h"
#include "cost_store.h"

/* EMA weight of a new on-board latency observation. The first execution of a
   model after boot carries the VX graph compile (~13 s against a warm 0.5-4 s),
   so a raw last-value would poison that n for the rest of the run; 0.3 lets a
   point recover in a handful of executions while still tracking. */
#define LATENCY_EMA_ALPHA 0.3f

int cost_model_correction_enabled(void)
{
    static int enabled = -1;
    if (enabled == -1)
    {
        const char *e = getenv("DIPP_LATENCY_CORRECTION");
        enabled = (e && strcmp(e, "0") == 0) ? 0 : 1;
    }
    return enabled;
}

const char *cost_source_name(CostSource src)
{
    switch (src)
    {
    case COST_SRC_POINT:
        return "point";
    case COST_SRC_MODEL:
        return "model";
    case COST_SRC_HINT:
        return "hint";
    default:
        return "none";
    }
}

CostSource cost_model_predict(const ModuleParameterList *cfg, int n, uint32_t *lat_us, float *en_uwh)
{
    if (cost_store != NULL)
    {
        int idx = cost_store_find_point(cost_store, cfg->hash, n);
        if (idx != -1)
        {
            *lat_us = cost_store->items[idx].lat_us;
            *en_uwh = cost_store->items[idx].en_uwh;
            return COST_SRC_POINT;
        }
        idx = cost_store_find_model(cost_store, cfg->hash);
        if (idx != -1)
        {
            const CostEntry *m = &cost_store->items[idx];
            float dn = (float)(n - m->n);
            float lat = (float)m->lat_us + m->lat_slope_us * dn;
            float en = m->en_uwh + m->en_slope_uwh * dn;
            *lat_us = lat > 0.0f ? (uint32_t)lat : 0u;
            *en_uwh = en > 0.0f ? en : 0.0f;
            return COST_SRC_MODEL;
        }
    }
    *lat_us = (uint32_t)cfg->latency_cost;
    *en_uwh = (float)cfg->energy_cost;
    return COST_SRC_HINT;
}

void cost_model_observe_latency(const ModuleParameterList *cfg, int n, long measured_us, float pred_en_uwh)
{
    if (cost_store == NULL || cost_store_impl == NULL || !cost_model_correction_enabled())
        return;
    if (measured_us < 0)
        return;

    CostEntry e;
    int idx = cost_store_find_point(cost_store, cfg->hash, n);
    if (idx != -1)
    {
        e = cost_store->items[idx];
        float ema = (1.0f - LATENCY_EMA_ALPHA) * (float)e.lat_us + LATENCY_EMA_ALPHA * (float)measured_us;
        e.lat_us = (uint32_t)(ema + 0.5f);
        e.n_obs += 1;
        /* A seeded point that has taken an on-board observation is no longer
           the offline value; mark it so the analysis can tell them apart. */
        e.origin = COST_ORIGIN_ONBOARD;
    }
    else
    {
        memset(&e, 0, sizeof(e));
        e.hash = cfg->hash;
        e.kind = COST_KIND_POINT;
        e.origin = COST_ORIGIN_ONBOARD;
        e.n = n;
        e.n_obs = 1;
        e.lat_us = (uint32_t)measured_us;
        e.en_uwh = pred_en_uwh;
    }
    e.valid = 1;
    cost_store_impl->put(cost_store, &e);
    printf("COST_OBSERVE hash=%u n=%d lat_meas_us=%ld lat_point_us=%u n_obs=%u en_uwh=%.3f\n",
           cfg->hash, n, measured_us, e.lat_us, e.n_obs, e.en_uwh);
}
