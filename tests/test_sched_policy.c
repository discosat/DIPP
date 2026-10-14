/* Host test of the scheduling-policy decisions and parameters (sched_policy.c).
 *
 *   gcc -DDIPP_HOST_TEST -Isrc/include -o /tmp/test_sched_policy \
 *       tests/test_sched_policy.c src/heuristics/sched_policy.c && /tmp/test_sched_policy
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include "sched_policy.h"

#define QCAP 100u   /* MAX_QUEUE_SIZE */

static int fails = 0;
static void check(const char *what, int ok)
{
    printf("  %-66s %s\n", what, ok ? "PASS" : "FAIL");
    fails += !ok;
}

static void clear_env(void)
{
    const char *v[] = {"DIPP_SCHED_Q_MED", "DIPP_SCHED_Q_LOW", "DIPP_SCHED_GATE_MED_MS", "DIPP_SCHED_GATE_LOW_MS",
                       "DIPP_SCHED_GRANULARITY", "DIPP_SCHED_WINDOW", "DIPP_SCHED_FRAME_OVERHEAD_MS",
                       "DIPP_SCHED_GUARD", "DIPP_SCHED_ACCEPT", "DIPP_SCHED_K_X10", "DIPP_SCHED_SMAX_HIGH_MS",
                       "DIPP_SCHED_SMAX_MED_MS", "DIPP_SCHED_ENERGY"};
    for (size_t i = 0; i < sizeof(v) / sizeof(v[0]); i++)
        unsetenv(v[i]);
    setenv("DIPP_SCHED_POWER_BUDGET_MW", "3365", 1);   /* DISCO-2 BOL: 16.435 W - 13.07 W */
}

#define S 1000000LL
/* n frames captured period_us apart from t0, deadline window win_us, queue depth depth. */
static int64_t arrive(int64_t t0, int n, int64_t period_us, int64_t win_us, uint32_t depth)
{
    for (int i = 0; i < n; i++)
        sched_obs_note_arrival(t0 + i * period_us, t0 + i * period_us + win_us, depth, 5);
    return t0 + (n - 1) * period_us;
}

/* One frame through a 5-module pipeline whose lowest-effort latencies sum to sum_us. */
static void frame(int64_t sum_us)
{
    int64_t part = sum_us / 5;
    for (uint32_t i = 0; i < 5; i++)
        sched_obs_note_module(i, (uint32_t)(i < 4 ? part : sum_us - 4 * part), 100.0);
}

int main(void)
{
    printf("sched_policy\n");

    /* Tiers */
    check("depth below q_med -> high", sched_tier_for_depth(11, 12, 20) == SCHED_TIER_HIGH);
    check("depth == q_med -> medium (boundary belongs to the lower tier)",
          sched_tier_for_depth(12, 12, 20) == SCHED_TIER_MEDIUM);
    check("depth in [q_med, q_low) -> medium", sched_tier_for_depth(19, 12, 20) == SCHED_TIER_MEDIUM);
    check("depth == q_low -> lowest", sched_tier_for_depth(20, 12, 20) == SCHED_TIER_LOWEST);
    check("q_med == q_low reproduces the two-tier policy (no medium tier)",
          sched_tier_for_depth(4, 5, 5) == SCHED_TIER_HIGH && sched_tier_for_depth(5, 5, 5) == SCHED_TIER_LOWEST);
    check("q = inf never leaves high (deadline-greedy)",
          sched_tier_for_depth(100000, SCHED_Q_INF, SCHED_Q_INF) == SCHED_TIER_HIGH);
    check("q_med = 0 skips high entirely", sched_tier_for_depth(0, 0, 20) == SCHED_TIER_MEDIUM);

    /* Yield */
    check("earlier waiting deadline + can park -> yield", sched_should_yield(1000, 999, 1) == 1);
    check("equal deadline does not yield (no ping-pong among equals)", sched_should_yield(1000, 1000, 1) == 0);
    check("later waiting deadline does not yield", sched_should_yield(1000, 2000, 1) == 0);
    check("nothing waiting (INT64_MAX) does not yield", sched_should_yield(1000, INT64_MAX, 1) == 0);
    check("cannot park (partial queue full) does not yield", sched_should_yield(1000, 1, 0) == 0);

    /* Parameters */
    clear_env();
    check("defaults load", sched_params_init() == 0);
    check("default thresholds auto (from the budget)", sched_get_q_med() == SCHED_Q_AUTO && sched_get_q_low() == SCHED_Q_AUTO);
    check("default gates 5 s / 3 s (split acceptance only)",
          sched_get_gate_med_us() == 5000000 && sched_get_gate_low_us() == 3000000);
    check("default granularity stage, guard on, feasibility acceptance, k = 2",
          sched_get_granularity() == SCHED_GRAN_STAGE && sched_get_guard() && sched_get_accept() == SCHED_ACCEPT_FEAS &&
              sched_get_k_x10() == 20);
    check("default window 8, overhead 780 ms, s_max 12150 / 7330 ms",
          sched_get_window() == 8 && sched_get_frame_overhead_ms() == 780 && sched_get_smax_high_ms() == 12150 &&
              sched_get_smax_med_ms() == 7330);
    setenv("DIPP_SCHED_Q_MED", "6", 1);
    setenv("DIPP_SCHED_Q_LOW", "inf", 1);
    setenv("DIPP_SCHED_GRANULARITY", "batch", 1);
    setenv("DIPP_SCHED_GUARD", "off", 1);
    setenv("DIPP_SCHED_ACCEPT", "split", 1);
    setenv("DIPP_SCHED_K_X10", "15", 1);
    check("env overrides load", sched_params_init() == 0);
    check("env values in force (6 / inf / batch / off / split / k 1.5)",
          sched_get_q_med() == 6 && sched_get_q_low() == SCHED_Q_INF && sched_get_granularity() == SCHED_GRAN_BATCH &&
              !sched_get_guard() && sched_get_accept() == SCHED_ACCEPT_SPLIT && sched_get_k_x10() == 15);
    clear_env(); setenv("DIPP_SCHED_Q_MED", "twelve", 1);
    check("non-numeric threshold refused", sched_params_init() != 0);
    clear_env(); setenv("DIPP_SCHED_GRANULARITY", "module", 1);
    check("unknown granularity refused", sched_params_init() != 0);
    clear_env(); setenv("DIPP_SCHED_Q_MED", "30", 1); setenv("DIPP_SCHED_Q_LOW", "20", 1);
    check("q_med above q_low refused", sched_params_init() != 0);
    clear_env(); setenv("DIPP_SCHED_Q_MED", "12", 1);
    check("one threshold auto and one explicit refused", sched_params_init() != 0);
    clear_env(); setenv("DIPP_SCHED_WINDOW", "0", 1);
    check("window 0 refused", sched_params_init() != 0);
    clear_env(); setenv("DIPP_SCHED_K_X10", "5", 1);
    check("k below 1 refused", sched_params_init() != 0);
    clear_env(); setenv("DIPP_SCHED_ACCEPT", "maybe", 1);
    check("unknown acceptance refused", sched_params_init() != 0);
    clear_env(); unsetenv("DIPP_SCHED_POWER_BUDGET_MW");
    check("energy on without the stated power budget refused", sched_params_init() != 0);
    clear_env(); unsetenv("DIPP_SCHED_POWER_BUDGET_MW"); setenv("DIPP_SCHED_ENERGY", "off", 1);
    check("energy off needs no budget", sched_params_init() == 0);
    clear_env();
    sched_params_init();

    /* Observations and estimates */
    int64_t t0;
    SchedGuard g0;
    sched_obs_reset();
    check("no frames -> no estimates", sched_s_low_us(5) == -1 && sched_cadence_us() == -1 && sched_budget_us(5, QCAP) == -1);
    uint32_t qm, ql;
    sched_thresholds(5, QCAP, &qm, &ql);
    check("no budget yet -> auto thresholds do not restrict", qm == SCHED_Q_INF && ql == SCHED_Q_INF);
    arrive(0, 9, 3340000, 300 * S, 0);
    for (int i = 0; i < 8; i++)
        frame(4760000);
    check("S_low = sum of per-module medians + overhead = 5.54 s", sched_s_low_us(5) == 5540000);
    check("cadence = median inter-capture = 3.34 s", sched_cadence_us() == 3340000);
    check("budget = max window / k = 150 s", sched_budget_us(5, QCAP) == 150 * S);
    sched_thresholds(5, QCAP, &qm, &ql);
    check("auto thresholds at 300 s: floor(150/12.15) = 12, floor(150/7.33) = 20", qm == 12 && ql == 20);
    sched_obs_note_arrival(40 * S, 60 * S, 0, 5);
    check("budget follows the most lenient window in play (a 20 s frame does not shrink it)",
          sched_budget_us(5, QCAP) == 150 * S);
    check("rest of a started frame from module 2 at lowest = 3 module medians",
          sched_rest_low_us(2, 5, 1) == 3 * (4760000 / 5) + (4760000 - 4 * (4760000 / 5)) - (4760000 / 5));
    sched_obs_reset();
    for (int i = 0; i < 9; i++)
        sched_obs_note_arrival(i * 2 * S, i * 2 * S + 20 * S, 0, 5);
    check("only tight frames: budget = 20 s / 2 = 10 s", sched_budget_us(5, QCAP) == 10 * S);
    sched_thresholds(5, QCAP, &qm, &ql);
    check("... thresholds 0 / 1 (lowest effort once anything queues)", qm == 0 && ql == 1);

    /* Capacity-aware budget: with a 6 h window the queue (100 frames, drained
       at lowest effort in 100 * 5.54 s) binds before the deadline does */
    sched_obs_reset();
    t0 = arrive(0, 9, 3340000, 21600 * S, 0);
    for (int i = 0; i < 8; i++)
        frame(4760000);
    check("6 h windows: budget = min(10800 s, 100 * 5.54 s) = 554 s", sched_budget_us(5, QCAP) == 554 * S);
    sched_thresholds(5, QCAP, &qm, &ql);
    check("... thresholds floor(554/12.15) = 45, floor(554/7.33) = 75", qm == 45 && ql == 75);
    check("a larger queue (300) moves the bound (1662 s)", sched_budget_us(5, 300) == 1662 * S);
    g0 = sched_guard_eval(t0 + 1 * S, 50, 5, QCAP);
    check("queue binds, no history: violation and full queue coincide -> does not fire",
          !g0.fires && g0.t_rem_s > 420 && g0.t_rem_s < 421);
    g0 = sched_guard_eval(t0 + 1 * S, 100, 5, QCAP);
    check("queue already full -> horizon 0, does not fire", !g0.fires && g0.t_rem_s == 0.0);

    /* Guard */
    sched_obs_reset();
    int64_t t = arrive(0, 9, 3340000, 300 * S, 0);
    for (int i = 0; i < 8; i++)
        frame(4760000);
    SchedGuard g = sched_guard_eval(t + 1 * S, 0, 5, QCAP);
    check("overloaded (rho 1.66), first episode, no history -> fires", g.fires && g.rho > 1.65 && g.rho < 1.67);
    g = sched_guard_eval(t + 6 * S, 0, 5, QCAP);
    check("no capture within S_low (episode over) -> does not fire", !g.fires);
    clear_env(); setenv("DIPP_SCHED_GUARD", "off", 1); sched_params_init();
    check("guard off -> never fires", !sched_guard_eval(t + 1 * S, 0, 5, QCAP).fires);
    clear_env(); sched_params_init();
    /* a finished 10-frame episode, a gap, then a new one: history says episodes
       last ~30 s; with an empty queue the budget (150 s) is 228 s away */
    sched_obs_reset();
    for (int i = 0; i < 8; i++)
        frame(4760000);
    t = arrive(0, 10, 3340000, 300 * S, 0);
    t = arrive(t + 600 * S, 3, 3340000, 300 * S, 0);
    g = sched_guard_eval(t + 1 * S, 0, 5, QCAP);
    check("short overloads in history and room in the budget -> does not fire", !g.fires && g.t_rem_s < 31);
    g = sched_guard_eval(t + 1 * S, 26, 5, QCAP);
    check("same, but 26 frames queued (backlog past the budget) -> fires", g.fires);
    t = arrive(t + 3340000, 10, 3340000, 300 * S, 0);   /* the episode goes on past 30 s */
    g = sched_guard_eval(t + 1 * S, 0, 5, QCAP);
    check("an episode outlasting all history: horizon = time until the queue is full",
          g.t_rem_s > 840 && g.t_rem_s < 842);   /* 100 * 5.54 / (5.54/3.34 - 1) = 841 s */
    sched_obs_reset();
    t = arrive(0, 9, 8000000, 300 * S, 0);
    for (int i = 0; i < 8; i++)
        frame(4760000);
    check("not overloaded (rho 0.69) -> never fires", !sched_guard_eval(t + 1 * S, 50, 5, QCAP).fires);

    /* Feasibility acceptance */
    SchedFrame cur = {300 * S, 0, 100 * S};
    check("own path within half the window -> feasible", sched_feasible(0, &cur, NULL, 0, 20, NULL));
    cur.rest_us = 151 * S;
    check("own path beyond half the window -> refused", !sched_feasible(0, &cur, NULL, 0, 20, NULL));
    SchedFrame urgent2[1] = {{25 * S, 5 * S, 16 * S}};
    SchedFrame c1 = {20 * S, 0, 10 * S};
    check("a queued urgent frame that would be pushed late -> refused", !sched_feasible(0, &c1, urgent2, 1, 20, NULL));
    c1.rest_us = 9 * S;
    check("the same with room for both -> feasible", sched_feasible(0, &c1, urgent2, 1, 20, NULL));
    SchedFrame late[1] = {{5 * S, 0, 10 * S}};
    SchedFrame c2 = {300 * S, 0, 50 * S};
    check("an earlier frame already late at lowest -> refused (as in the lab)", !sched_feasible(0, &c2, late, 1, 20, NULL));
    SchedFrame later_late[1] = {{301 * S, 0, 400 * S}};
    check("a later frame infeasible even without this upgrade does not block it",
          sched_feasible(0, &c2, later_late, 1, 20, NULL));

    /* Energy-timed feasibility */
    clear_env(); sched_params_init();
    SchedEnergy al = {41.6 + 0.001, 41.6, 3.5, 3.3, 60.0, 1};   /* always on, 1 mWh above the floor */
    check("recharge, board always on: 1 mWh at 0.2 W net = 18 s",
          sched_recharge_s(&al, 0.001) > 17.99 && sched_recharge_s(&al, 0.001) < 18.01);
    SchedEnergy bu = al;
    bu.always = 0;
    check("recharge, per burst: 10 mWh = 60 s hold at 0.2 W + 24 J at 3.5 W = 66.9 s",
          sched_recharge_s(&bu, 0.01) > 66.8 && sched_recharge_s(&bu, 0.01) < 66.9);
    SchedEnergy dead = al;
    dead.p_s_w = 3.0;
    check("no net charge with the board on -> cannot recharge", sched_recharge_s(&dead, 0.001) < 0);
    /* a 10 s frame drawing 4 mWh of module energy, 1 mWh above the floor, 3.5 W in, 3.3 W idle:
       need = 4 mWh - 0.2 W * 10 s = 3.44 mWh -> wait 2.44 mWh / 0.2 W = 44 s */
    SchedFrame ef = {300 * S, 0, 10 * S, 4000.0};
    check("enough slack: feasible with the recharge wait", sched_feasible(0, &ef, NULL, 0, 20, &al));
    ef.deadline_us = 50 * S;   /* path 10 s within 25 s, but 44 s wait + 10 s = 54 s > 50 s */
    check("short deadline: the recharge wait makes it late -> refused", !sched_feasible(0, &ef, NULL, 0, 20, &al));
    check("... the same without energy is feasible (time only)", sched_feasible(0, &ef, NULL, 0, 20, NULL));
    SchedEnergy full = al;
    full.w_wh = 80.0;
    check("battery well above the floor: no wait, feasible", sched_feasible(0, &ef, NULL, 0, 20, &full));
    SchedFrame q1[1] = {{62 * S, 0, 10 * S, 0.0}};   /* runs after f: 54 s + 10 s = 64 s > 62 s */
    SchedFrame ef2 = {60 * S, 0, 10 * S, 4000.0};
    check("an upgrade whose recharge pushes a queued frame late -> refused",
          !sched_feasible(0, &ef2, q1, 1, 20, &al) && sched_feasible(0, &ef2, q1, 1, 20, NULL));

    /* Learned available power */
    sched_obs_reset();
    check("no battery history -> the stated budget (3.365 W)",
          sched_power_available_w() > 3.364 && sched_power_available_w() < 3.366);
    for (int i = 0; i <= 600; i++)   /* 6000 s, orbit 5725.8 s: battery +0.5 W net, payload 3 W */
        sched_obs_note_battery(i * 10.0, 50.0 + 0.5 * i * 10.0 / 3600.0, 3.0 * i * 10.0 / 3600.0, 5725.8);
    check("after one orbit -> learned from the trend: 0.5 W + 3 W payload = 3.5 W",
          sched_power_available_w() > 3.49 && sched_power_available_w() < 3.51);

    printf(fails ? "FAILED (%d failures)\n" : "OK (0 failures)\n", fails);
    return fails != 0;
}
