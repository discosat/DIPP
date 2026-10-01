#include "battery_simulator.h"

#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <math.h>
#include <time.h>
#include <pthread.h>

#ifdef DIPP_HOST_TEST
/* Host unit test: no libparam. The stored energy is a plain float. */
static float host_battery_level_wh = 0.0f;
static float level_get(void) { return host_battery_level_wh; }
static void level_set(float wh) { host_battery_level_wh = wh; }
#else
#include "dipp_battery_param.h"
static float level_get(void) { return param_get_float(&battery_level); }
static void level_set(float wh) { param_set_float(&battery_level, wh); }
#endif

/* Set by battery_simulator_init before any reader starts (header). */
float battery_safety_margin_wh = 0.0f;

static CubeSatBatterySimulator g_sim;
static int g_sim_ready = 0;
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;

static int64_t now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

/* A required number. Returns 0 and sets *out, or -1 after logging why. */
static int req_d(const char *name, double *out)
{
    const char *v = getenv(name);
    if (!v || !*v)
    {
        printf("BATTERY_CONFIG_ERROR var=%s reason=unset\n", name);
        return -1;
    }
    char *end;
    double x = strtod(v, &end);
    if (end == v || *end != '\0' || !isfinite(x))
    {
        printf("BATTERY_CONFIG_ERROR var=%s reason=not_a_number value=%s\n", name, v);
        return -1;
    }
    *out = x;
    return 0;
}

static int bad(const char *name, const char *reason, double value)
{
    printf("BATTERY_CONFIG_ERROR var=%s reason=%s value=%g\n", name, reason, value);
    return -1;
}

/* Profile file: "# comment" and "phase p_gen_w" lines, phase in [0,1)
   strictly ascending, p_gen_w >= 0, at least two samples. Anything else is
   an error, not a partial read. Returns 0 or -1. */
static int load_profile(const char *path, CubeSatBatterySimulator *sim)
{
    FILE *f = fopen(path, "r");
    if (!f)
    {
        printf("BATTERY_CONFIG_ERROR var=DIPP_BATT_PROFILE reason=open_failed path=%s\n", path);
        return -1;
    }
    int cap = 64, n = 0, lineno = 0, err = 0;
    double *ph = malloc(cap * sizeof(double));
    double *pw = malloc(cap * sizeof(double));
    char line[128];
    while (!err && fgets(line, sizeof(line), f))
    {
        lineno++;
        if (!strchr(line, '\n') && !feof(f))
        {
            printf("BATTERY_CONFIG_ERROR var=DIPP_BATT_PROFILE reason=line_too_long path=%s line=%d\n",
                   path, lineno);
            err = 1;
            break;
        }
        if (line[0] == '#' || line[0] == '\n')
            continue;
        double a, b;
        if (sscanf(line, "%lf %lf", &a, &b) != 2)
        {
            printf("BATTERY_CONFIG_ERROR var=DIPP_BATT_PROFILE reason=unparsable path=%s line=%d\n",
                   path, lineno);
            err = 1;
            break;
        }
        if (a < 0.0 || a >= 1.0 || (n && a <= ph[n - 1]) || b < 0.0 || !isfinite(b))
        {
            printf("BATTERY_CONFIG_ERROR var=DIPP_BATT_PROFILE reason=bad_sample path=%s line=%d "
                   "phase=%g w=%g\n", path, lineno, a, b);
            err = 1;
            break;
        }
        if (n == cap)
        {
            cap *= 2;
            ph = realloc(ph, cap * sizeof(double));
            pw = realloc(pw, cap * sizeof(double));
        }
        ph[n] = a;
        pw[n] = b;
        n++;
    }
    fclose(f);
    if (!err && n < 2)
    {
        printf("BATTERY_CONFIG_ERROR var=DIPP_BATT_PROFILE reason=fewer_than_2_samples path=%s\n", path);
        err = 1;
    }
    if (err)
    {
        free(ph);
        free(pw);
        return -1;
    }
    sim->profile_phase = ph;
    sim->profile_w = pw;
    sim->n_profile = n;
    return 0;
}

double battery_simulator_p_gen(const CubeSatBatterySimulator *sim, double phase)
{
    phase -= floor(phase);
    /* Piecewise linear with wrap-around between the last and first sample. */
    int n = sim->n_profile;
    int i = 0;
    while (i < n && sim->profile_phase[i] <= phase)
        i++;
    /* sample i-1 <= phase < sample i */
    double p0, w0, p1, w1;
    if (i == 0)
    {
        p0 = sim->profile_phase[n - 1] - 1.0;
        w0 = sim->profile_w[n - 1];
        p1 = sim->profile_phase[0];
        w1 = sim->profile_w[0];
    }
    else if (i == n)
    {
        p0 = sim->profile_phase[n - 1];
        w0 = sim->profile_w[n - 1];
        p1 = sim->profile_phase[0] + 1.0;
        w1 = sim->profile_w[0];
    }
    else
    {
        p0 = sim->profile_phase[i - 1];
        w0 = sim->profile_w[i - 1];
        p1 = sim->profile_phase[i];
        w1 = sim->profile_w[i];
    }
    if (p1 <= p0)
        return w0;
    return w0 + (w1 - w0) * (phase - p0) / (p1 - p0);
}

int battery_simulator_configure(CubeSatBatterySimulator *sim)
{
    memset(sim, 0, sizeof(*sim));
    int err = 0;
    double capacity = 0, soc0 = 0, bus = 0, orbit_min = 0, phase0 = 0;
    err |= req_d("DIPP_BATT_CAPACITY_WH", &capacity);
    err |= req_d("DIPP_BATT_SOC0", &soc0);
    err |= req_d("DIPP_BATT_BUS_W", &bus);
    err |= req_d("DIPP_BATT_ORBIT_MIN", &orbit_min);
    err |= req_d("DIPP_BATT_PHASE0", &phase0);
    if (!err)
    {
        if (capacity <= 0.0)
            err |= bad("DIPP_BATT_CAPACITY_WH", "not_positive", capacity);
        if (soc0 <= 0.0 || soc0 > 1.0)
            err |= bad("DIPP_BATT_SOC0", "outside_(0,1]", soc0);
        if (bus < 0.0)
            err |= bad("DIPP_BATT_BUS_W", "negative", bus);
        if (orbit_min <= 0.0)
            err |= bad("DIPP_BATT_ORBIT_MIN", "not_positive", orbit_min);
    }

    const char *profile = getenv("DIPP_BATT_PROFILE");
    if (!profile || !*profile)
    {
        printf("BATTERY_CONFIG_ERROR var=DIPP_BATT_PROFILE reason=unset\n");
        err = -1;
    }
    else if (load_profile(profile, sim) != 0)
        err = -1;

    const char *mode = getenv("DIPP_BATT_BOARD_MODE");
    if (!mode || !*mode)
    {
        printf("BATTERY_CONFIG_ERROR var=DIPP_BATT_BOARD_MODE reason=unset\n");
        err = -1;
    }
    else if (strcmp(mode, "burst") == 0)
        sim->board_mode = BOARD_BURST;
    else if (strcmp(mode, "always") == 0)
        sim->board_mode = BOARD_ALWAYS;
    else if (strcmp(mode, "off") == 0)
        sim->board_mode = BOARD_OFF;
    else
    {
        printf("BATTERY_CONFIG_ERROR var=DIPP_BATT_BOARD_MODE reason=not_burst_always_off value=%s\n", mode);
        err = -1;
    }
    if (mode && strcmp(mode, "off") != 0)
    {
        if (req_d("DIPP_BATT_BOARD_IDLE_W", &sim->board_idle_W) != 0)
            err = -1;
        else if (sim->board_idle_W < 0.0)
            err |= bad("DIPP_BATT_BOARD_IDLE_W", "negative", sim->board_idle_W);
        if (req_d("DIPP_BATT_BOARD_HOLD_S", &sim->board_hold_s) != 0)
            err = -1;
        else if (sim->board_hold_s < 0.0)
            err |= bad("DIPP_BATT_BOARD_HOLD_S", "negative", sim->board_hold_s);
    }

    /* Margin: explicit, or derived from RESERVE_FRAC -- one of the two. */
    const char *margin_env = getenv("DIPP_BATT_MARGIN_WH");
    int margin_explicit = margin_env && *margin_env;
    double margin = 0, reserve = 0;
    if (margin_explicit)
    {
        if (req_d("DIPP_BATT_MARGIN_WH", &margin) != 0)
            err = -1;
    }
    else if (req_d("DIPP_BATT_RESERVE_FRAC", &reserve) != 0)
    {
        printf("BATTERY_CONFIG_ERROR var=DIPP_BATT_MARGIN_WH reason=neither_margin_nor_reserve_frac_set\n");
        err = -1;
    }
    else if (reserve < 0.0 || reserve >= 1.0)
        err |= bad("DIPP_BATT_RESERVE_FRAC", "outside_[0,1)", reserve);

    if (err)
    {
        free(sim->profile_phase);
        free(sim->profile_w);
        memset(sim, 0, sizeof(*sim));
        return -1;
    }

    sim->total_capacity_Wh = capacity;
    sim->constant_load_W = bus;
    sim->orbit_period_s = orbit_min * 60.0;
    sim->phase0 = phase0 - floor(phase0);
    sim->current_time_s = 0.0;
    sim->board_on = (sim->board_mode == BOARD_ALWAYS);

    /* Eclipse length of the profile: the time the platform carries its own
       load with nothing coming in. */
    int dark = 0;
    double avg = 0.0;
    for (int k = 0; k < 3600; k++)
    {
        double w = battery_simulator_p_gen(sim, (k + 0.5) / 3600.0);
        dark += w <= 0.5 ? 1 : 0;
        avg += w;
    }
    avg /= 3600.0;
    sim->eclipse_s = sim->orbit_period_s * dark / 3600.0;

    /* What one orbit of generation leaves after the platform's own load: the
       most the payload may spend per orbit and still have the battery full
       again one orbit later (header). */
    double orbit_surplus_wh = (avg - bus) * sim->orbit_period_s / 3600.0;
    battery_safety_margin_wh =
        (float)(margin_explicit ? margin : capacity * (1.0 + reserve) - orbit_surplus_wh);
    if (battery_safety_margin_wh >= capacity)
        printf("BATTERY_MARGIN_ABOVE_CAPACITY margin_wh=%.2f capacity_wh=%.2f orbit_surplus_wh=%.3f "
               "-- the payload can never run on this platform\n",
               battery_safety_margin_wh, capacity, orbit_surplus_wh);

    sim->soc = soc0;
    double initial = capacity * soc0;
    level_set((float)initial);
    sim->p_gen_now_W = battery_simulator_p_gen(sim, sim->phase0);
    sim->current_state = sim->p_gen_now_W > 0.5 ? SUNLIT : ECLIPSE;

    printf("BATTERY_CONFIG capacity_wh=%.2f soc0=%.3f wh0=%.3f bus_w=%.2f gen_avg_w=%.3f "
           "orbit_min=%.3f eclipse_min=%.2f phase0=%.3f orbit_surplus_wh=%.3f margin_wh=%.2f "
           "margin_src=%s reserve_frac=%.3f "
           "profile=%s profile_samples=%d board_mode=%s board_idle_w=%.2f board_hold_s=%.1f\n",
           capacity, soc0, initial, bus, avg, orbit_min, sim->eclipse_s / 60.0, sim->phase0,
           orbit_surplus_wh, battery_safety_margin_wh, margin_explicit ? "env" : "derived", reserve,
           profile,
           sim->n_profile,
           sim->board_mode == BOARD_BURST ? "burst" : sim->board_mode == BOARD_ALWAYS ? "always" : "off",
           sim->board_idle_W, sim->board_hold_s);
    return 0;
}

void battery_simulator_step(CubeSatBatterySimulator *sim, double dt_s)
{
    double phase = sim->current_time_s / sim->orbit_period_s + sim->phase0;
    double power_in_W = battery_simulator_p_gen(sim, phase);

    /* The processing board, powered or not over this step (header). */
    int on;
    if (sim->board_mode == BOARD_ALWAYS)
        on = 1;
    else if (sim->board_mode == BOARD_OFF)
        on = 0;
    else
        on = sim->board_busy ||
             (sim->board_ever_active && sim->current_time_s - sim->board_last_active_s < sim->board_hold_s);
    if (on != sim->board_on)
    {
        sim->board_on = on;
        if (on)
            sim->board_power_ons++;
        printf("BOARD_POWER ts_us=%lld sim_t_s=%.2f state=%s power_ons=%ld board_wh=%.4f\n",
               (long long)now_us(), sim->current_time_s, on ? "on" : "off", sim->board_power_ons,
               sim->board_wh);
    }
    double board_W = on ? sim->board_idle_W : 0.0;
    sim->board_wh += board_W * dt_s / 3600.0;

    double net_power_W = power_in_W - sim->constant_load_W - board_W;
    double energy_change_Wh = (net_power_W * dt_s) / 3600.0;

    double level = level_get() + energy_change_Wh;
    if (level <= 0.0 && !sim->empty_logged)
    {
        sim->empty_logged = 1;
        printf("BATTERY_EMPTY ts_us=%lld sim_t_s=%.2f board=%s -- the scenario exceeds the platform\n",
               (long long)now_us(), sim->current_time_s, on ? "on" : "off");
    }
    level = fmax(0.0, fmin(sim->total_capacity_Wh, level));
    level_set((float)level);
    sim->soc = level / sim->total_capacity_Wh;
    sim->current_time_s += dt_s;

    /* The integration uses the power at the START of the step (forward
       Euler), but what is reported is the state NOW -- otherwise a
       BATTERY_FF line ending mid-sunlight prints the phase it reached
       beside the eclipse it left, and the eclipse shading in the timeline
       is drawn from a state that is one step stale. At 10 ms steps the two
       are indistinguishable; across a 1 s fast-forward step they are not. */
    sim->p_gen_now_W = battery_simulator_p_gen(
        sim, sim->current_time_s / sim->orbit_period_s + sim->phase0);
    sim->current_state = sim->p_gen_now_W > 0.5 ? SUNLIT : ECLIPSE;
}

int battery_simulator_init(void)
{
    pthread_mutex_lock(&g_lock);
    int rc = g_sim_ready ? 0 : battery_simulator_configure(&g_sim);
    if (rc == 0)
        g_sim_ready = 1;
    pthread_mutex_unlock(&g_lock);
    return rc;
}

/* Use before init is a programming error, not a condition to paper over. */
static void require_ready_locked(const char *caller)
{
    if (!g_sim_ready)
    {
        fprintf(stderr, "battery simulator: %s called before battery_simulator_init\n", caller);
        abort();
    }
}

static void log_state(const char *tag)
{
    double phase = g_sim.current_time_s / g_sim.orbit_period_s + g_sim.phase0;
    phase -= floor(phase);
    printf("%s ts_us=%lld sim_t_s=%.2f wh=%.4f soc=%.4f phase=%.4f state=%s p_gen_w=%.2f "
           "board=%s board_wh=%.4f\n",
           tag, (long long)now_us(), g_sim.current_time_s, (double)level_get(), g_sim.soc, phase,
           g_sim.current_state == SUNLIT ? "SUNLIT" : "ECLIPSE", g_sim.p_gen_now_W,
           g_sim.board_on ? "on" : "off", g_sim.board_wh);
}

void simulate_battery(void)
{
    pthread_mutex_lock(&g_lock);
    require_ready_locked("simulate_battery");
    log_state("BATTERY");
    double next_log = BATTERY_LOG_PERIOD_S;
    pthread_mutex_unlock(&g_lock);

    /* Step by the wall time that actually elapsed, not a nominal 10 ms: the
       usleep plus the step itself make every pass slightly longer than the
       period, so a fixed step let simulated time fall ~1 % behind the wall
       clock -- and the trace harness places frames at "wall elapsed + time
       fast-forwarded", which is only right if the two stay equal. */
    int64_t last_us = now_us();
    while (1)
    {
        pthread_mutex_lock(&g_lock);
        int64_t t_us = now_us();
        double dt = (t_us - last_us) / 1e6;
        last_us = t_us;
        battery_simulator_step(&g_sim, dt);
        if (g_sim.current_time_s >= next_log)
        {
            log_state("BATTERY");
            /* A fast-forward may have jumped past several periods. */
            next_log = (floor(g_sim.current_time_s / BATTERY_LOG_PERIOD_S) + 1.0) * BATTERY_LOG_PERIOD_S;
        }
        pthread_mutex_unlock(&g_lock);
        usleep(SIMULATION_UPDATE_PERIOD_US);
    }
}

void battery_simulator_advance(double seconds)
{
    if (seconds <= 0.0)
        return;
    pthread_mutex_lock(&g_lock);
    require_ready_locked("battery_simulator_advance");
    double t0 = g_sim.current_time_s;
    float wh0 = level_get();
    double board_wh0 = g_sim.board_wh;
    double left = seconds;
    while (left > 0.0)
    {
        double dt = left > 1.0 ? 1.0 : left;
        battery_simulator_step(&g_sim, dt);
        left -= dt;
    }
    double phase = g_sim.current_time_s / g_sim.orbit_period_s + g_sim.phase0;
    phase -= floor(phase);
    printf("BATTERY_FF ts_us=%lld adv_s=%.1f sim_t_before_s=%.2f sim_t_after_s=%.2f "
           "wh_before=%.4f wh_after=%.4f phase_after=%.4f state=%s board_wh=%.4f\n",
           (long long)now_us(), seconds, t0, g_sim.current_time_s, (double)wh0, (double)level_get(),
           phase, g_sim.current_state == SUNLIT ? "SUNLIT" : "ECLIPSE", g_sim.board_wh - board_wh0);
    pthread_mutex_unlock(&g_lock);
}

void battery_simulator_set_wh(float wh)
{
    pthread_mutex_lock(&g_lock);
    require_ready_locked("battery_simulator_set_wh");
    double level = fmax(0.0, fmin(g_sim.total_capacity_Wh, (double)wh));
    level_set((float)level);
    g_sim.soc = level / g_sim.total_capacity_Wh;
    printf("BATTERY_SET ts_us=%lld wh=%.4f soc=%.4f\n", (long long)now_us(), level, g_sim.soc);
    pthread_mutex_unlock(&g_lock);
}

void battery_simulator_board_activity(void)
{
    pthread_mutex_lock(&g_lock);
    require_ready_locked("battery_simulator_board_activity");
    g_sim.board_ever_active = 1;
    g_sim.board_last_active_s = g_sim.current_time_s;
    pthread_mutex_unlock(&g_lock);
}

void battery_simulator_board_busy(int busy)
{
    pthread_mutex_lock(&g_lock);
    require_ready_locked("battery_simulator_board_busy");
    g_sim.board_ever_active = 1;
    g_sim.board_busy = busy ? 1 : 0;
    g_sim.board_last_active_s = g_sim.current_time_s;
    pthread_mutex_unlock(&g_lock);
}

double battery_simulator_time_s(void)
{
    pthread_mutex_lock(&g_lock);
    require_ready_locked("battery_simulator_time_s");
    double t = g_sim.current_time_s;
    pthread_mutex_unlock(&g_lock);
    return t;
}

double battery_simulator_phase(void)
{
    pthread_mutex_lock(&g_lock);
    require_ready_locked("battery_simulator_phase");
    double phase = g_sim.current_time_s / g_sim.orbit_period_s + g_sim.phase0;
    pthread_mutex_unlock(&g_lock);
    return phase - floor(phase);
}

float get_battery_level_wh(void)
{
    return level_get();
}

void put_load_on_battery(float load_uWh)
{
    pthread_mutex_lock(&g_lock);
    require_ready_locked("put_load_on_battery");
    float battery_level_wh = level_get();
    float new_capacity = fmax(0.0, battery_level_wh - (load_uWh / 1000000.0));
    if (new_capacity <= 0.0f && !g_sim.empty_logged)
    {
        g_sim.empty_logged = 1;
        printf("BATTERY_EMPTY ts_us=%lld sim_t_s=%.2f cause=execution_debit -- the scenario exceeds the platform\n",
               (long long)now_us(), g_sim.current_time_s);
    }
    level_set(new_capacity);
    g_sim.soc = new_capacity / g_sim.total_capacity_Wh;
    pthread_mutex_unlock(&g_lock);
}
