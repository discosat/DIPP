#ifndef DIPP_SCHED_PARAM_H
#define DIPP_SCHED_PARAM_H

#include <param/param.h>
#include "dipp_paramids.h"

/* Scheduling-policy parameters (src/include/sched_policy.h). RAM params,
   settable over CSP; set at start-up from compiled defaults or the
   DIPP_SCHED_* environment (sched_params_init). Included by sched_policy.c
   only. */
static uint32_t _sched_q_med, _sched_q_low, _sched_gate_med_ms, _sched_gate_low_ms, _sched_window,
    _sched_frame_overhead_ms, _sched_k_x10, _sched_smax_high_ms, _sched_smax_med_ms;
static uint32_t _sched_power_budget_mw;
static uint8_t _sched_granularity, _sched_guard, _sched_accept, _sched_energy;
PARAM_DEFINE_STATIC_RAM(PARAMID_SCHED_Q_MED, sched_q_med, PARAM_TYPE_UINT32, -1, 0, PM_CONF, NULL, NULL,
                        &_sched_q_med, "Depth at which effort is capped at medium (4294967294 auto, 4294967295 never)");
PARAM_DEFINE_STATIC_RAM(PARAMID_SCHED_Q_LOW, sched_q_low, PARAM_TYPE_UINT32, -1, 0, PM_CONF, NULL, NULL,
                        &_sched_q_low, "Depth at which the lowest effort is used (4294967294 auto, 4294967295 never)");
PARAM_DEFINE_STATIC_RAM(PARAMID_SCHED_GATE_MED_MS, sched_gate_med_ms, PARAM_TYPE_UINT32, -1, 0, PM_CONF, NULL,
                        NULL, &_sched_gate_med_ms, "SPLIT acceptance: budget below which medium is considered (ms)");
PARAM_DEFINE_STATIC_RAM(PARAMID_SCHED_GATE_LOW_MS, sched_gate_low_ms, PARAM_TYPE_UINT32, -1, 0, PM_CONF, NULL,
                        NULL, &_sched_gate_low_ms, "SPLIT acceptance: budget below which low is considered (ms)");
PARAM_DEFINE_STATIC_RAM(PARAMID_SCHED_GRANULARITY, sched_granularity, PARAM_TYPE_UINT8, -1, 0, PM_CONF, NULL,
                        NULL, &_sched_granularity, "0 = batch, 1 = stage (yield at module boundaries)");
PARAM_DEFINE_STATIC_RAM(PARAMID_SCHED_WINDOW, sched_window, PARAM_TYPE_UINT32, -1, 0, PM_CONF, NULL, NULL,
                        &_sched_window, "Estimator window, frames (1-32)");
PARAM_DEFINE_STATIC_RAM(PARAMID_SCHED_FRAME_OVERHEAD_MS, sched_frame_overhead_ms, PARAM_TYPE_UINT32, -1, 0, PM_CONF,
                        NULL, NULL, &_sched_frame_overhead_ms, "Per-frame time outside modules, ms");
PARAM_DEFINE_STATIC_RAM(PARAMID_SCHED_GUARD, sched_guard, PARAM_TYPE_UINT8, -1, 0, PM_CONF, NULL, NULL,
                        &_sched_guard, "Overload guard: 0 off, 1 on");
PARAM_DEFINE_STATIC_RAM(PARAMID_SCHED_ACCEPT, sched_accept, PARAM_TYPE_UINT8, -1, 0, PM_CONF, NULL, NULL,
                        &_sched_accept, "Per-module acceptance: 0 split (original), 1 feasibility");
PARAM_DEFINE_STATIC_RAM(PARAMID_SCHED_K_X10, sched_k_x10, PARAM_TYPE_UINT32, -1, 0, PM_CONF, NULL, NULL,
                        &_sched_k_x10, "Budget divisor k, times 10");
PARAM_DEFINE_STATIC_RAM(PARAMID_SCHED_SMAX_HIGH_MS, sched_smax_high_ms, PARAM_TYPE_UINT32, -1, 0, PM_CONF, NULL,
                        NULL, &_sched_smax_high_ms, "Per-frame time at high effort on worst-case content, ms");
PARAM_DEFINE_STATIC_RAM(PARAMID_SCHED_SMAX_MED_MS, sched_smax_med_ms, PARAM_TYPE_UINT32, -1, 0, PM_CONF, NULL,
                        NULL, &_sched_smax_med_ms, "Per-frame time at medium effort on worst-case content, ms");
PARAM_DEFINE_STATIC_RAM(PARAMID_SCHED_ENERGY, sched_energy, PARAM_TYPE_UINT8, -1, 0, PM_CONF, NULL, NULL,
                        &_sched_energy, "Energy-timed feasibility and wait rule: 0 off, 1 on");
PARAM_DEFINE_STATIC_RAM(PARAMID_SCHED_POWER_BUDGET_MW, sched_power_budget_mw, PARAM_TYPE_UINT32, -1, 0, PM_CONF,
                        NULL, NULL, &_sched_power_budget_mw,
                        "Stated orbit-average power available to the payload (generation - bus), mW; used until one orbit of battery history exists (4294967295 unset)");
#endif
