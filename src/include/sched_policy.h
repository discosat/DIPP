#ifndef SCHED_POLICY_H
#define SCHED_POLICY_H

#include <stddef.h>
#include <stdint.h>

/* SIPP's default scheduling policy: its runtime parameters, what it observes,
 * and its pure decisions, kept apart from the daemon so they can be unit-tested
 * on the host (tests/test_sched_policy.c). Specification and evidence:
 * paper-rev/policy_v2_spec.md; simulator: tools/policy_lab.py (policy "C3").
 *
 * The policy knows only per-frame quantities: each frame's capture time a_f
 * and deadline d_f (both CLOCK_MONOTONIC us, set by the producer). There are
 * no frame classes. One design constant, k (default 2).
 *
 * Budget. B = max over the last W frames of (d_f - a_f), divided by k: the
 * backlog may not exceed 1/k of the most lenient deadline in play.
 *
 * Effort tier from the total queue depth D, before each batch:
 *   D <  Q1             best effort over all levels (high first)
 *   Q1 <= D < Q2        best effort capped at medium
 *   D >= Q2             lowest effort
 * with Q_e = floor(B / s_e_max), s_e_max the per-frame time at effort e on
 * worst-case content (parameters, from the cost model). Explicit Q values
 * override ("auto" by default; inf/inf = deadline-only; 5/5 = the original
 * two-tier policy).
 *
 * Overload guard (tier -> lowest). S_low = per-frame time at lowest effort
 * (median over the last W frames of the cost model's lowest-effort latency of
 * each module at the input sizes those frames had, + frame overhead);
 * C = median inter-capture time; rho = S_low / C. An overload episode is a
 * run of captures whose gaps are no longer than (depth at the previous
 * capture + 1) * S_low; T_hat = the longest finished episode, tau = time since
 * the current one began. The guard fires iff rho >= 1, a capture arrived within
 * S_low, and
 *     T_v = (B - D * S_low) / (rho - 1)  <  T_rem,
 *     T_rem = T_hat - tau (or infinite before any episode or once tau > T_hat):
 * even the lowest effort is falling behind, and the lowest-effort backlog would
 * reach the budget before this overload is expected to end.
 *
 * Per-module acceptance (accept = FEAS). An effort above the lowest is taken
 * only if (1) the frame's planned completion -- this module at that effort,
 * every later module at lowest -- stays within a_f + (d_f - a_f) / k, and
 * (2) inserting that path into the deadline order of every queued frame, each
 * at lowest effort, leaves every feasible frame feasible. accept = SPLIT keeps
 * the original rule (slack / modules left, with the 5 s / 3 s gates) for the
 * baselines.
 *
 * Granularity: BATCH runs a started batch to completion; STAGE returns to the
 * scheduler after each module when a frame with an earlier deadline is
 * waiting, parking the current batch through the checkpoint-on-halt path.
 */

#define SCHED_Q_INF UINT32_MAX
#define SCHED_Q_AUTO (UINT32_MAX - 1)
#define SCHED_MAX_WINDOW 32
#define SCHED_MAX_MODULES 16
#define SCHED_MAX_QUEUED 256

typedef enum
{
    SCHED_TIER_HIGH = 0,   /* best effort, all levels */
    SCHED_TIER_MEDIUM = 1, /* best effort, capped at medium */
    SCHED_TIER_LOWEST = 2  /* lowest effort */
} SchedTier;

typedef enum
{
    SCHED_GRAN_BATCH = 0,
    SCHED_GRAN_STAGE = 1
} SchedGranularity;

typedef enum
{
    SCHED_ACCEPT_SPLIT = 0, /* original: slack / modules left, gated */
    SCHED_ACCEPT_FEAS = 1   /* feasibility-preserving, with the per-frame path budget */
} SchedAccept;

/* ------------------------------------------------------------ decisions */
SchedTier sched_tier_for_depth(uint32_t depth, uint32_t q_med, uint32_t q_low);
const char *sched_tier_name(SchedTier t);

/* Yield at a module boundary iff a waiting frame has a strictly earlier
 * deadline and the current batch can be parked. best_other_us = INT64_MAX
 * when nothing else waits. */
int sched_should_yield(int64_t cur_deadline_us, int64_t best_other_us, int can_park);

/* A queued frame for the feasibility test: its deadline, capture time and the
 * time it still needs (for the frame being decided: its planned path). */
typedef struct
{
    int64_t deadline_us;
    int64_t capture_us;
    int64_t rest_us;
    double energy_uwh; /* module energy of that remaining work (cost model) */
} SchedFrame;

/* Energy state for the energy-timed walk (sched_energy on). The walk spends
 * each piece of work's module energy plus the board's idle power and earns the
 * available power p_s_w; where the battery would fall below floor_wh it
 * inserts the time to recharge: at p_s_w - p_idle_w while the board stays on
 * (always; or for hold_s when switched per burst), then at p_s_w. */
typedef struct
{
    double w_wh;     /* battery now */
    double floor_wh; /* safety floor (battery_safety_margin_wh) */
    double p_s_w;    /* available power: generation - bus, orbit average */
    double p_idle_w; /* board power while on and idle */
    double hold_s;   /* per-burst board: on for this long after the last activity */
    int always;      /* board always on */
} SchedEnergy;

/* Seconds the battery needs to gain deficit_wh while the payload waits; -1 if
 * it cannot (no net charge). */
double sched_recharge_s(const SchedEnergy *en, double deficit_wh);

/* Feasibility-preserving acceptance (accept = FEAS), pure: 1 = the upgrade may
 * be taken. others need not be sorted; at most SCHED_MAX_QUEUED are used.
 * en = NULL: time only; otherwise the walk integrates the battery and adds
 * recharge time (energy-timed feasibility). */
int sched_feasible(int64_t now_us, const SchedFrame *cur, const SchedFrame *others, size_t n_others,
                   uint32_t k_x10, const SchedEnergy *en);

/* ------------------------------------------------------------ observations */
void sched_obs_reset(void);
/* A frame was ingested: capture and deadline times, queue depth before it, and
 * the number of modules of its pipeline. */
void sched_obs_note_arrival(int64_t capture_us, int64_t deadline_us, uint32_t depth_before, uint32_t n_modules);
/* Module idx of some frame ran on its actual input; low_pred_us = the cost
 * model's latency of that module's LOWEST-effort configuration at that input. */
void sched_obs_note_module(uint32_t idx, uint32_t low_pred_us, double low_pred_uwh);
/* Battery telemetry for learning the available power: simulated time t_s,
 * stored energy and the payload's cumulative energy (board + modules), Wh. */
void sched_obs_note_battery(double t_s, double w_wh, double payload_wh, double orbit_s);
/* Available power (generation - bus, W), averaged over the last orbit of
 * telemetry; before one orbit exists, the stated budget (sched_power_budget). */
double sched_power_available_w(void);

/* Estimates; -1 while unknown. */
int64_t sched_s_low_us(uint32_t n_modules);                       /* per frame, incl. overhead */
int64_t sched_rest_low_us(uint32_t from_idx, uint32_t n_modules, int started); /* modules from_idx.. */
double sched_rest_low_uwh(uint32_t from_idx, uint32_t n_modules);  /* their module energy */
int64_t sched_cadence_us(void);
/* B = min(max window / k, queue_cap * S_low): a full ingestion queue drops
 * frames, so it is a deadline too, reached when queue_cap frames wait and the
 * lowest effort needs queue_cap * S_low to drain them. queue_cap is the
 * daemon's MAX_QUEUE_SIZE, passed explicitly; while S_low is unknown (no
 * module has run yet) only the deadline term exists. */
int64_t sched_budget_us(uint32_t n_modules, uint32_t queue_cap);
void sched_thresholds(uint32_t n_modules, uint32_t queue_cap, uint32_t *q_med, uint32_t *q_low); /* Q1, Q2 */

/* Guard decision and the quantities behind it (for logging). */
typedef struct
{
    int fires;
    double rho;          /* -1 unknown */
    double t_v_s;        /* time until the lowest-effort backlog reaches B */
    double t_rem_s;      /* expected remaining overload (from history, else time
                            until the ingestion queue is full) */
    double budget_s;
} SchedGuard;
SchedGuard sched_guard_eval(int64_t now_us, uint32_t depth, uint32_t n_modules, uint32_t queue_cap);

/* ------------------------------------------------------------ parameters */
/* Runtime parameters (CSP params on the board; env overrides for the harness,
 * applied once by sched_params_init, which logs SCHED_CONFIG and refuses
 * invalid values). Read on every decision. */
int sched_params_init(void);
uint32_t sched_get_q_med(void);       /* may be SCHED_Q_AUTO / SCHED_Q_INF */
uint32_t sched_get_q_low(void);
int64_t sched_get_gate_med_us(void);
int64_t sched_get_gate_low_us(void);
SchedGranularity sched_get_granularity(void);
uint32_t sched_get_window(void);
uint32_t sched_get_frame_overhead_ms(void);
int sched_get_guard(void);
SchedAccept sched_get_accept(void);
uint32_t sched_get_k_x10(void);
uint32_t sched_get_smax_high_ms(void);
uint32_t sched_get_smax_med_ms(void);
int sched_get_energy(void);
double sched_get_power_budget_w(void); /* < 0 when unset */

/* The tier chosen for the batch being executed (set by update_heuristic). */
extern SchedTier sched_current_tier;

#endif
