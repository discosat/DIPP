#include "sched_policy.h"

#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Compiled defaults. Every value in force is logged in SCHED_CONFIG together
   with where it came from (default | env). Sources: paper-rev/policy_v2_spec.md. */
#define DEFAULT_Q_MED SCHED_Q_AUTO
#define DEFAULT_Q_LOW SCHED_Q_AUTO
#define DEFAULT_GATE_MED_MS 5000u        /* SPLIT acceptance only (original policy) */
#define DEFAULT_GATE_LOW_MS 3000u
#define DEFAULT_GRANULARITY SCHED_GRAN_STAGE
#define DEFAULT_WINDOW 8u                /* estimator window; 4-16 give the same p99 regret */
#define DEFAULT_FRAME_OVERHEAD_MS 780u   /* measured: 0.30 s/batch + 0.41 s/ingested frame, rounded up */
#define DEFAULT_GUARD 1u
#define DEFAULT_ACCEPT SCHED_ACCEPT_FEAS
#define DEFAULT_K_X10 20u                /* k = 2 */
#define DEFAULT_SMAX_HIGH_MS 12150u      /* worst-case content (all tiles kept), cost model + 0.3 s */
#define DEFAULT_SMAX_MED_MS 7330u
#define DEFAULT_ENERGY 1u                /* energy-timed feasibility + wait rule */
#define POWER_BUDGET_UNSET UINT32_MAX    /* the stated budget is a platform value: no default */

SchedTier sched_current_tier = SCHED_TIER_HIGH;

SchedTier sched_tier_for_depth(uint32_t depth, uint32_t q_med, uint32_t q_low)
{
    if (depth < q_med)
        return SCHED_TIER_HIGH;
    if (depth < q_low)
        return SCHED_TIER_MEDIUM;
    return SCHED_TIER_LOWEST;
}

const char *sched_tier_name(SchedTier t)
{
    switch (t)
    {
    case SCHED_TIER_HIGH:
        return "high";
    case SCHED_TIER_MEDIUM:
        return "medium";
    case SCHED_TIER_LOWEST:
        return "lowest";
    }
    return "?";
}

int sched_should_yield(int64_t cur_deadline_us, int64_t best_other_us, int can_park)
{
    return can_park && best_other_us < cur_deadline_us;
}

/* ------------------------------------------------------------ feasibility */
static int cmp_frame_deadline(const void *a, const void *b)
{
    int64_t x = ((const SchedFrame *)a)->deadline_us, y = ((const SchedFrame *)b)->deadline_us;
    return (x > y) - (x < y);
}

double sched_recharge_s(const SchedEnergy *en, double deficit_wh)
{
    if (deficit_wh <= 0)
        return 0.0;
    double x = deficit_wh * 3600.0; /* J */
    double on = en->p_s_w - en->p_idle_w;
    if (en->always)
        return on > 0 ? x / on : -1.0;
    double gain_hold = on > 0 ? on * en->hold_s : 0.0;
    if (on > 0 && x <= gain_hold)
        return x / on;
    x -= gain_hold;
    return en->p_s_w > 0 ? en->hold_s + x / en->p_s_w : -1.0;
}

/* One piece of work in the walk: dur_us of time drawing energy_uwh of module
   energy. Advances *tc and *w; returns 0 if the battery cannot be recharged. */
static int walk_step(const SchedEnergy *en, int64_t *tc, double *w, int64_t dur_us, double energy_uwh)
{
    if (en)
    {
        double need = energy_uwh / 1e6 + (en->p_idle_w - en->p_s_w) * (dur_us / 1e6) / 3600.0;
        if (*w - need < en->floor_wh)
        {
            double r = sched_recharge_s(en, en->floor_wh + need - *w);
            if (r < 0)
                return 0;
            *tc += (int64_t)(r * 1e6);
            *w = en->floor_wh + need;
        }
        *w -= need;
    }
    *tc += dur_us;
    return 1;
}

int sched_feasible(int64_t now_us, const SchedFrame *cur, const SchedFrame *others, size_t n_others,
                   uint32_t k_x10, const SchedEnergy *en)
{
    /* (1) the frame's own planned path within 1/k of its deadline window */
    if (k_x10 > 0)
    {
        int64_t window = cur->deadline_us - cur->capture_us;
        if (now_us + cur->rest_us > cur->capture_us + window * 10 / (int64_t)k_x10)
            return 0;
    }
    /* (2) deadline order over every queued frame, each at lowest effort */
    static SchedFrame tmp[SCHED_MAX_QUEUED];
    size_t n = n_others < SCHED_MAX_QUEUED ? n_others : SCHED_MAX_QUEUED;
    memcpy(tmp, others, n * sizeof(SchedFrame));
    qsort(tmp, n, sizeof(SchedFrame), cmp_frame_deadline);
    int64_t tc = now_us;
    double w = en ? en->w_wh : 0.0;
    int placed = 0;
    for (size_t i = 0; i < n; i++)
    {
        if (!placed && cur->deadline_us <= tmp[i].deadline_us)
        {
            if (!walk_step(en, &tc, &w, cur->rest_us, cur->energy_uwh) || tc > cur->deadline_us)
                return 0;
            placed = 1;
        }
        int64_t before = tc;
        if (!walk_step(en, &tc, &w, tmp[i].rest_us, tmp[i].energy_uwh))
            return 0;
        if (tc > tmp[i].deadline_us)
        {
            /* already infeasible without this frame's upgrade: not its doing */
            if (placed && before - cur->rest_us > tmp[i].deadline_us - tmp[i].rest_us)
                continue;
            return 0;
        }
    }
    if (!placed)
    {
        if (!walk_step(en, &tc, &w, cur->rest_us, cur->energy_uwh) || tc > cur->deadline_us)
            return 0;
    }
    return 1;
}

/* ------------------------------------------------------------ observations */
typedef struct
{
    int64_t v[SCHED_MAX_WINDOW];
    uint32_t n, next;
} Ring;

static Ring obs_iat;                        /* inter-capture times */
static Ring obs_win;                        /* deadline windows d - a */
static Ring obs_low[SCHED_MAX_MODULES];     /* per-module lowest-effort latency at actual inputs */
static Ring obs_low_en[SCHED_MAX_MODULES];  /* ... and module energy, nWh */

/* Battery telemetry, one sample per >= BAT_SAMPLE_S, enough for one orbit */
#define BAT_SAMPLES 1024
#define BAT_SAMPLE_S 10.0
static struct { double t, w, payload; } bat_s[BAT_SAMPLES];
static uint32_t bat_n, bat_next;
static double bat_orbit_s = -1.0;
static int64_t obs_last_capture = -1;
static uint32_t obs_last_depth;
static int64_t ep_start = -1;               /* current overload episode (lowest effort) */
static int64_t ep_longest = -1;             /* longest finished episode */

static void ring_push(Ring *r, int64_t x)
{
    r->v[r->next] = x;
    r->next = (r->next + 1) % SCHED_MAX_WINDOW;
    if (r->n < SCHED_MAX_WINDOW)
        r->n++;
}

static int cmp_i64(const void *a, const void *b)
{
    int64_t x = *(const int64_t *)a, y = *(const int64_t *)b;
    return (x > y) - (x < y);
}

static uint32_t win_len(void)
{
    uint32_t w = sched_get_window();
    return w > SCHED_MAX_WINDOW ? SCHED_MAX_WINDOW : (w == 0 ? 1 : w);
}

/* Median (or max) of the most recent w entries; -1 if empty. */
static int64_t ring_stat(const Ring *r, int want_max)
{
    uint32_t w = win_len();
    uint32_t m = r->n < w ? r->n : w;
    if (m == 0)
        return -1;
    int64_t tmp[SCHED_MAX_WINDOW];
    for (uint32_t i = 0; i < m; i++)
        tmp[i] = r->v[(r->next + SCHED_MAX_WINDOW - 1 - i) % SCHED_MAX_WINDOW];
    qsort(tmp, m, sizeof(tmp[0]), cmp_i64);
    if (want_max)
        return tmp[m - 1];
    return (m % 2) ? tmp[m / 2] : (tmp[m / 2 - 1] + tmp[m / 2]) / 2;
}

void sched_obs_reset(void)
{
    memset(&obs_iat, 0, sizeof(obs_iat));
    memset(&obs_win, 0, sizeof(obs_win));
    memset(obs_low, 0, sizeof(obs_low));
    memset(obs_low_en, 0, sizeof(obs_low_en));
    bat_n = bat_next = 0;
    bat_orbit_s = -1.0;
    obs_last_capture = -1;
    obs_last_depth = 0;
    ep_start = ep_longest = -1;
}

void sched_obs_note_module(uint32_t idx, uint32_t low_pred_us, double low_pred_uwh)
{
    if (idx < SCHED_MAX_MODULES)
    {
        ring_push(&obs_low[idx], low_pred_us);
        ring_push(&obs_low_en[idx], (int64_t)(low_pred_uwh * 1000.0));
    }
}

double sched_rest_low_uwh(uint32_t from_idx, uint32_t n_modules)
{
    double x = 0.0;
    for (uint32_t i = from_idx; i < n_modules && i < SCHED_MAX_MODULES; i++)
    {
        int64_t m = ring_stat(&obs_low_en[i], 0);
        if (m > 0)
            x += m / 1000.0;
    }
    return x;
}

void sched_obs_note_battery(double t_s, double w_wh, double payload_wh, double orbit_s)
{
    bat_orbit_s = orbit_s;
    if (bat_n > 0)
    {
        uint32_t last = (bat_next + BAT_SAMPLES - 1) % BAT_SAMPLES;
        if (t_s - bat_s[last].t < BAT_SAMPLE_S)
            return;
    }
    bat_s[bat_next].t = t_s;
    bat_s[bat_next].w = w_wh;
    bat_s[bat_next].payload = payload_wh;
    bat_next = (bat_next + 1) % BAT_SAMPLES;
    if (bat_n < BAT_SAMPLES)
        bat_n++;
}

double sched_power_available_w(void)
{
    /* the newest sample at least one orbit older than the newest one */
    if (bat_n >= 2 && bat_orbit_s > 0)
    {
        uint32_t newest = (bat_next + BAT_SAMPLES - 1) % BAT_SAMPLES;
        for (uint32_t k = 1; k < bat_n; k++)
        {
            uint32_t i = (newest + BAT_SAMPLES - k) % BAT_SAMPLES;
            double dt = bat_s[newest].t - bat_s[i].t;
            if (dt >= bat_orbit_s)
                return ((bat_s[newest].w - bat_s[i].w) + (bat_s[newest].payload - bat_s[i].payload)) * 3600.0 / dt;
        }
    }
    return sched_get_power_budget_w();
}

int64_t sched_rest_low_us(uint32_t from_idx, uint32_t n_modules, int started)
{
    if (n_modules == 0 || n_modules > SCHED_MAX_MODULES)
        return -1;
    int64_t x = started ? 0 : (int64_t)sched_get_frame_overhead_ms() * 1000;
    for (uint32_t i = from_idx; i < n_modules; i++)
    {
        int64_t m = ring_stat(&obs_low[i], 0);
        if (m < 0)
            return -1;
        x += m;
    }
    return x;
}

int64_t sched_s_low_us(uint32_t n_modules)
{
    return sched_rest_low_us(0, n_modules, 0);
}

int64_t sched_cadence_us(void)
{
    return ring_stat(&obs_iat, 0);
}

int64_t sched_budget_us(uint32_t n_modules, uint32_t queue_cap)
{
    int64_t w = ring_stat(&obs_win, 1);
    uint32_t k = sched_get_k_x10();
    if (w < 0 || k == 0)
        return -1;
    int64_t b = w * 10 / (int64_t)k;
    int64_t s_low = sched_s_low_us(n_modules);
    if (s_low > 0 && (int64_t)queue_cap * s_low < b)
        b = (int64_t)queue_cap * s_low;
    return b;
}

void sched_obs_note_arrival(int64_t capture_us, int64_t deadline_us, uint32_t depth_before, uint32_t n_modules)
{
    ring_push(&obs_win, deadline_us - capture_us);
    int64_t s_low = sched_s_low_us(n_modules);
    if (obs_last_capture < 0)
        ep_start = capture_us;
    else
    {
        if (capture_us > obs_last_capture)
            ring_push(&obs_iat, capture_us - obs_last_capture);
        /* an episode ends at a gap long enough for the lowest effort to drain
           the backlog the previous frame found */
        if (s_low > 0 && capture_us - obs_last_capture > ((int64_t)obs_last_depth + 1) * s_low)
        {
            int64_t d = obs_last_capture - ep_start;
            if (d > ep_longest)
                ep_longest = d;
            ep_start = capture_us;
        }
    }
    obs_last_capture = capture_us;
    obs_last_depth = depth_before;
}

void sched_thresholds(uint32_t n_modules, uint32_t queue_cap, uint32_t *q_med, uint32_t *q_low)
{
    uint32_t qm = sched_get_q_med(), ql = sched_get_q_low();
    int64_t B = sched_budget_us(n_modules, queue_cap);
    if (qm == SCHED_Q_AUTO)
        qm = B < 0 ? SCHED_Q_INF : (uint32_t)(B / ((int64_t)sched_get_smax_high_ms() * 1000));
    if (ql == SCHED_Q_AUTO)
        ql = B < 0 ? SCHED_Q_INF : (uint32_t)(B / ((int64_t)sched_get_smax_med_ms() * 1000));
    *q_med = qm;
    *q_low = ql;
}

SchedGuard sched_guard_eval(int64_t now_us, uint32_t depth, uint32_t n_modules, uint32_t queue_cap)
{
    SchedGuard g = {0, -1.0, 0.0, 1e30, -1.0};
    int64_t B = sched_budget_us(n_modules, queue_cap);
    if (B >= 0)
        g.budget_s = B / 1e6;
    if (!sched_get_guard())
        return g;
    int64_t s_low = sched_s_low_us(n_modules), cad = sched_cadence_us();
    if (s_low <= 0 || cad <= 0 || B < 0)
        return g;
    g.rho = (double)s_low / (double)cad;
    if (g.rho < 1.0 || obs_last_capture < 0 || now_us - obs_last_capture > s_low)
        return g;
    double w = (double)depth * s_low / 1e6, b = B / 1e6;
    double tau = ep_start >= 0 ? (now_us - ep_start) / 1e6 : 0.0;
    if (g.rho == 1.0)
    {
        g.fires = w >= b;
        return g;
    }
    if (ep_longest < 0 || tau > ep_longest / 1e6)
    {
        /* no experience, or beyond it: the overload can last at most until the
           ingestion queue is full (from then on frames are dropped whatever
           the effort); it fills at (rho - 1) / S_low frames per second */
        double room = depth < queue_cap ? (double)(queue_cap - depth) : 0.0;
        g.t_rem_s = room * (s_low / 1e6) / (g.rho - 1.0);
    }
    else
        g.t_rem_s = ep_longest / 1e6 - tau;
    g.t_v_s = (b - w) / (g.rho - 1.0);
    g.fires = g.t_v_s < g.t_rem_s;
    return g;
}

/* ------------------------------------------------------------- parameters */
#ifdef DIPP_HOST_TEST
static uint32_t _q_med = DEFAULT_Q_MED, _q_low = DEFAULT_Q_LOW;
static uint32_t _gate_med_ms = DEFAULT_GATE_MED_MS, _gate_low_ms = DEFAULT_GATE_LOW_MS;
static uint8_t _granularity = DEFAULT_GRANULARITY, _guard = DEFAULT_GUARD, _accept = DEFAULT_ACCEPT;
static uint32_t _window = DEFAULT_WINDOW, _frame_overhead_ms = DEFAULT_FRAME_OVERHEAD_MS, _k_x10 = DEFAULT_K_X10;
static uint32_t _smax_high_ms = DEFAULT_SMAX_HIGH_MS, _smax_med_ms = DEFAULT_SMAX_MED_MS;
static uint8_t _energy = DEFAULT_ENERGY;
static uint32_t _power_budget_mw = POWER_BUDGET_UNSET;
#define GET_U32(name) (_##name)
#define SET_U32(name, v) (_##name = (v))
#define GET_U8(name) (_##name)
#define SET_U8(name, v) (_##name = (v))
#else
#include "dipp_sched_param.h"
#define GET_U32(name) param_get_uint32(&sched_##name)
#define SET_U32(name, v) param_set_uint32(&sched_##name, (v))
#define GET_U8(name) param_get_uint8(&sched_##name)
#define SET_U8(name, v) param_set_uint8(&sched_##name, (v))
#endif

uint32_t sched_get_q_med(void) { return GET_U32(q_med); }
uint32_t sched_get_q_low(void) { return GET_U32(q_low); }
int64_t sched_get_gate_med_us(void) { return (int64_t)GET_U32(gate_med_ms) * 1000; }
int64_t sched_get_gate_low_us(void) { return (int64_t)GET_U32(gate_low_ms) * 1000; }
SchedGranularity sched_get_granularity(void) { return GET_U8(granularity) ? SCHED_GRAN_STAGE : SCHED_GRAN_BATCH; }
uint32_t sched_get_window(void) { return GET_U32(window); }
uint32_t sched_get_frame_overhead_ms(void) { return GET_U32(frame_overhead_ms); }
int sched_get_guard(void) { return GET_U8(guard) != 0; }
SchedAccept sched_get_accept(void) { return GET_U8(accept) ? SCHED_ACCEPT_FEAS : SCHED_ACCEPT_SPLIT; }
uint32_t sched_get_k_x10(void) { return GET_U32(k_x10); }
uint32_t sched_get_smax_high_ms(void) { return GET_U32(smax_high_ms); }
uint32_t sched_get_smax_med_ms(void) { return GET_U32(smax_med_ms); }
int sched_get_energy(void) { return GET_U8(energy) != 0; }
double sched_get_power_budget_w(void)
{
    uint32_t v = GET_U32(power_budget_mw);
    return v == POWER_BUDGET_UNSET ? -1.0 : v / 1000.0;
}

/* Parse an unsigned env value ("inf" / "auto" allowed for thresholds). 0 =
   unset, 1 = set, -1 = invalid (reported). */
static int env_u32(const char *var, uint32_t *out, int q_ok)
{
    const char *s = getenv(var);
    if (!s || !*s)
        return 0;
    if (q_ok && strcmp(s, "inf") == 0)
    {
        *out = SCHED_Q_INF;
        return 1;
    }
    if (q_ok && strcmp(s, "auto") == 0)
    {
        *out = SCHED_Q_AUTO;
        return 1;
    }
    char *end;
    errno = 0;
    unsigned long v = strtoul(s, &end, 10);
    if (errno || *end || v >= SCHED_Q_AUTO)
    {
        printf("SCHED_CONFIG_ERROR var=%s value=%s reason=not_an_unsigned_integer\n", var, s);
        return -1;
    }
    *out = (uint32_t)v;
    return 1;
}

/* 0/1 flags and the two named enums. */
static int env_choice(const char *var, const char *a, const char *b, uint8_t *out)
{
    const char *s = getenv(var);
    if (!s || !*s)
        return 0;
    if (strcmp(s, a) == 0)
        *out = 0;
    else if (strcmp(s, b) == 0)
        *out = 1;
    else
    {
        printf("SCHED_CONFIG_ERROR var=%s value=%s reason=not_%s_or_%s\n", var, s, a, b);
        return -1;
    }
    return 1;
}

static const char *fmt_q(uint32_t q, char *buf, size_t n)
{
    if (q == SCHED_Q_INF)
        snprintf(buf, n, "inf");
    else if (q == SCHED_Q_AUTO)
        snprintf(buf, n, "auto");
    else
        snprintf(buf, n, "%u", q);
    return buf;
}

int sched_params_init(void)
{
    int err = 0, r;
    uint32_t v;
    uint8_t c;
    const char *src[14];
    for (int i = 0; i < 14; i++)
        src[i] = "default";

    SET_U32(q_med, DEFAULT_Q_MED);
    SET_U32(q_low, DEFAULT_Q_LOW);
    SET_U32(gate_med_ms, DEFAULT_GATE_MED_MS);
    SET_U32(gate_low_ms, DEFAULT_GATE_LOW_MS);
    SET_U8(granularity, DEFAULT_GRANULARITY);
    SET_U32(window, DEFAULT_WINDOW);
    SET_U32(frame_overhead_ms, DEFAULT_FRAME_OVERHEAD_MS);
    SET_U8(guard, DEFAULT_GUARD);
    SET_U8(accept, DEFAULT_ACCEPT);
    SET_U32(k_x10, DEFAULT_K_X10);
    SET_U32(smax_high_ms, DEFAULT_SMAX_HIGH_MS);
    SET_U32(smax_med_ms, DEFAULT_SMAX_MED_MS);
    SET_U8(energy, DEFAULT_ENERGY);
    SET_U32(power_budget_mw, POWER_BUDGET_UNSET);

#define U32(var, name, i, qok) \
    if ((r = env_u32(var, &v, qok)) == 1) { SET_U32(name, v); src[i] = "env"; } else err |= (r < 0)
#define CH(var, a, b, name, i) \
    if ((r = env_choice(var, a, b, &c)) == 1) { SET_U8(name, c); src[i] = "env"; } else err |= (r < 0)
    U32("DIPP_SCHED_Q_MED", q_med, 0, 1);
    U32("DIPP_SCHED_Q_LOW", q_low, 1, 1);
    U32("DIPP_SCHED_GATE_MED_MS", gate_med_ms, 2, 0);
    U32("DIPP_SCHED_GATE_LOW_MS", gate_low_ms, 3, 0);
    CH("DIPP_SCHED_GRANULARITY", "batch", "stage", granularity, 4);
    U32("DIPP_SCHED_WINDOW", window, 5, 0);
    U32("DIPP_SCHED_FRAME_OVERHEAD_MS", frame_overhead_ms, 6, 0);
    CH("DIPP_SCHED_GUARD", "off", "on", guard, 7);
    CH("DIPP_SCHED_ACCEPT", "split", "feas", accept, 8);
    U32("DIPP_SCHED_K_X10", k_x10, 9, 0);
    U32("DIPP_SCHED_SMAX_HIGH_MS", smax_high_ms, 10, 0);
    U32("DIPP_SCHED_SMAX_MED_MS", smax_med_ms, 11, 0);
    CH("DIPP_SCHED_ENERGY", "off", "on", energy, 12);
    U32("DIPP_SCHED_POWER_BUDGET_MW", power_budget_mw, 13, 0);
#undef U32
#undef CH

    uint32_t qm = sched_get_q_med(), ql = sched_get_q_low();
    if ((qm == SCHED_Q_AUTO) != (ql == SCHED_Q_AUTO))
    {
        printf("SCHED_CONFIG_ERROR reason=q_auto_mixed: set both thresholds to auto or neither\n");
        err = 1;
    }
    else if (qm != SCHED_Q_AUTO && qm > ql)
    {
        printf("SCHED_CONFIG_ERROR reason=q_med_above_q_low q_med=%u q_low=%u\n", qm, ql);
        err = 1;
    }
    if (sched_get_window() == 0 || sched_get_window() > SCHED_MAX_WINDOW)
    {
        printf("SCHED_CONFIG_ERROR reason=window_out_of_range window=%u range=1..%d\n", sched_get_window(),
               SCHED_MAX_WINDOW);
        err = 1;
    }
    if (sched_get_k_x10() < 10)
    {
        printf("SCHED_CONFIG_ERROR reason=k_below_1 k_x10=%u\n", sched_get_k_x10());
        err = 1;
    }
    if (sched_get_smax_high_ms() == 0 || sched_get_smax_med_ms() == 0)
    {
        printf("SCHED_CONFIG_ERROR reason=smax_zero\n");
        err = 1;
    }
    if (sched_get_energy() && sched_get_power_budget_w() < 0)
    {
        printf("SCHED_CONFIG_ERROR reason=power_budget_unset: energy on needs DIPP_SCHED_POWER_BUDGET_MW "
               "(the platform's stated orbit-average generation minus bus load)\n");
        err = 1;
    }
    char a[16], b[16];
    printf("SCHED_CONFIG q_med=%s q_med_src=%s q_low=%s q_low_src=%s gate_med_ms=%u gate_med_src=%s "
           "gate_low_ms=%u gate_low_src=%s granularity=%s granularity_src=%s window=%u window_src=%s "
           "frame_overhead_ms=%u frame_overhead_src=%s guard=%s guard_src=%s accept=%s accept_src=%s "
           "k=%.1f k_src=%s smax_high_ms=%u smax_high_src=%s smax_med_ms=%u smax_med_src=%s "
           "energy=%s energy_src=%s power_budget_w=%.3f power_budget_src=%s\n",
           fmt_q(qm, a, sizeof(a)), src[0], fmt_q(ql, b, sizeof(b)), src[1],
           (unsigned)(sched_get_gate_med_us() / 1000), src[2], (unsigned)(sched_get_gate_low_us() / 1000), src[3],
           sched_get_granularity() == SCHED_GRAN_STAGE ? "stage" : "batch", src[4], sched_get_window(), src[5],
           sched_get_frame_overhead_ms(), src[6], sched_get_guard() ? "on" : "off", src[7],
           sched_get_accept() == SCHED_ACCEPT_FEAS ? "feas" : "split", src[8], sched_get_k_x10() / 10.0, src[9],
           sched_get_smax_high_ms(), src[10], sched_get_smax_med_ms(), src[11],
           sched_get_energy() ? "on" : "off", src[12], sched_get_power_budget_w(), src[13]);
    return err ? -1 : 0;
}
