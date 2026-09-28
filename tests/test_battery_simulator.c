/*
 * Battery / power model tests.
 *
 * The simulator has no defaults: every input comes from the environment and
 * configuration fails, naming what is missing or wrong, rather than
 * substitute a value. So the first half of this file is about refusing --
 * each way a platform can be incomplete or malformed must stop the daemon --
 * and the second half about the physics once it is configured:
 * battery_simulator_advance() (how a trace replay compresses the gaps
 * between imaging bursts) must put the same energy in the battery as living
 * through the gap, and the orbit's bookkeeping must not depend on which
 * caller did the integrating.
 *
 * Build natively; -DDIPP_HOST_TEST swaps libparam's battery_level for a
 * plain float (battery_simulator.c:11). Run from the repository root (the
 * digitised-profile cases read analysis/power-profile/):
 *   gcc -DDIPP_HOST_TEST -o /tmp/test_batt tests/test_battery_simulator.c \
 *       src/battery_simulator.c -Isrc/include -Isrc/include/param -lm -pthread
 *   /tmp/test_batt
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <math.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/wait.h>

#include "battery_simulator.h"

/* DISCO-II (experiments/platforms/disco2-bol.env). */
#define ORBIT_MIN 95.43
#define ORBIT_S (ORBIT_MIN * 60.0)
#define ECLIPSE_S (36.0 * 60.0)
#define SUNLIT_S (ORBIT_S - ECLIPSE_S)
#define GEN_DELIVERED_W (27.78 * 0.95) /* rectangular, after the power point tracker */
#define RECT_PROFILE "/tmp/sipp_batt_rect.profile"
#define TEST_PROFILE "/tmp/sipp_batt_test.profile"
#define BOL_PROFILE "analysis/power-profile/disco2_fig7-3_bol.profile"
#define EOL_PROFILE "analysis/power-profile/disco2_fig7-3_eol.profile"
#define CAPTURE "/tmp/sipp_batt_test.out"

static int failures = 0;
static void check(const char *what, int ok)
{
    printf("  %-62s %s\n", what, ok ? "PASS" : "FAIL");
    if (!ok)
        failures++;
}
static int close_to(double a, double b, double tol) { return fabs(a - b) < tol; }

static void env_set(const char *k, const char *v)
{
    if (v)
        setenv(k, v, 1);
    else
        unsetenv(k);
}

static const char *ALL_VARS[] = {
    "DIPP_BATT_CAPACITY_WH", "DIPP_BATT_SOC0", "DIPP_BATT_BUS_W", "DIPP_BATT_ORBIT_MIN",
    "DIPP_BATT_PHASE0", "DIPP_BATT_PROFILE", "DIPP_BATT_BOARD_MODE", "DIPP_BATT_BOARD_IDLE_W",
    "DIPP_BATT_BOARD_HOLD_S", "DIPP_BATT_RESERVE_FRAC", "DIPP_BATT_MARGIN_WH"};

static void clear_env(void)
{
    for (size_t i = 0; i < sizeof(ALL_VARS) / sizeof(*ALL_VARS); i++)
        unsetenv(ALL_VARS[i]);
}

/* An exact-edge rectangular profile, as tools/make_power_profile.py --rect
   writes it. */
static void write_rect(const char *path, double gen_w, double sunlit_frac)
{
    FILE *f = fopen(path, "w");
    fprintf(f, "# rectangular\n0.0 %.6f\n%.9f %.6f\n%.9f 0.0\n0.999999 0.0\n", gen_w,
            sunlit_frac - 1e-6, gen_w, sunlit_frac);
    fclose(f);
}

/* A complete DISCO-II platform on the rectangular profile. */
static void platform(void)
{
    clear_env();
    write_rect(RECT_PROFILE, GEN_DELIVERED_W, SUNLIT_S / ORBIT_S);
    env_set("DIPP_BATT_CAPACITY_WH", "92");
    env_set("DIPP_BATT_SOC0", "0.7");
    env_set("DIPP_BATT_BUS_W", "13.07");
    env_set("DIPP_BATT_ORBIT_MIN", "95.43");
    env_set("DIPP_BATT_PHASE0", "0");
    env_set("DIPP_BATT_PROFILE", RECT_PROFILE);
    env_set("DIPP_BATT_BOARD_MODE", "burst");
    env_set("DIPP_BATT_BOARD_IDLE_W", "3.30");
    env_set("DIPP_BATT_BOARD_HOLD_S", "60");
    env_set("DIPP_BATT_RESERVE_FRAC", "0.02");
}

/* The derived margin: capacity x (1 + reserve) - one orbit's surplus. */
static double want_margin(double gen_avg_w, double reserve)
{
    return 92.0 * (1.0 + reserve) - (gen_avg_w - 13.07) * ORBIT_S / 3600.0;
}

/* stdout -> file, for asserting on the log lines. */
static int capture_begin(void)
{
    fflush(stdout);
    int saved = dup(STDOUT_FILENO);
    int fd = open(CAPTURE, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    dup2(fd, STDOUT_FILENO);
    close(fd);
    return saved;
}

static char *capture_end(int saved)
{
    static char buf[32768];
    fflush(stdout);
    dup2(saved, STDOUT_FILENO);
    close(saved);
    FILE *f = fopen(CAPTURE, "r");
    size_t n = f ? fread(buf, 1, sizeof(buf) - 1, f) : 0;
    if (f)
        fclose(f);
    buf[n] = '\0';
    unlink(CAPTURE);
    return buf;
}

/* configure() with stdout captured; returns rc, *log the output. */
static int configure_quiet(CubeSatBatterySimulator *sim, char **log)
{
    int saved = capture_begin();
    int rc = battery_simulator_configure(sim);
    *log = capture_end(saved);
    return rc;
}

static int rejects(const char *what)
{
    CubeSatBatterySimulator sim;
    char *log;
    int rc = configure_quiet(&sim, &log);
    int ok = rc != 0 && strstr(log, "BATTERY_CONFIG_ERROR") && !strstr(log, "BATTERY_CONFIG ");
    if (!ok)
        printf("    (%s: rc=%d log=%s)\n", what, rc, log);
    return ok;
}

/* Independent host-side integration of the rectangular model. */
static double integrate_rect(double wh0, double t0_s, double seconds, double dt, double gen_w,
                             double bus_w, double orbit_s, double sunlit_s, double phase0,
                             double capacity)
{
    double wh = wh0, t = t0_s;
    for (double left = seconds; left > 0.0; left -= dt)
    {
        double step = left > dt ? dt : left;
        double phase = t / orbit_s + phase0;
        phase -= floor(phase);
        double p = (phase * orbit_s < sunlit_s) ? gen_w : 0.0;
        wh += (p - bus_w) * step / 3600.0;
        wh = fmax(0.0, fmin(capacity, wh));
        t += step;
    }
    return wh;
}

/* ------------------------------------------------------ refusing to start */

static void t_nothing_set(void)
{
    clear_env();
    CubeSatBatterySimulator sim;
    char *log;
    int rc = configure_quiet(&sim, &log);
    int all = 1;
    const char *names[] = {"CAPACITY_WH", "SOC0", "BUS_W", "ORBIT_MIN", "PHASE0",
                           "PROFILE", "BOARD_MODE", "MARGIN_WH"};
    for (size_t i = 0; i < sizeof(names) / sizeof(*names); i++)
        all &= strstr(log, names[i]) != NULL;
    check("nothing set: refuses, and names every missing input at once", rc != 0 && all);
}

static void t_complete_platform(void)
{
    platform();
    CubeSatBatterySimulator sim;
    char *log;
    int rc = configure_quiet(&sim, &log);
    check("a complete platform configures and logs BATTERY_CONFIG",
          rc == 0 && strstr(log, "BATTERY_CONFIG ") && !strstr(log, "ERROR"));
    check("SoC0 0.7 of 92 Wh -> 64.4 Wh stored", close_to(get_battery_level_wh(), 64.4, 1e-3));
    check("eclipse read from the profile: 36 min",
          close_to(sim.eclipse_s, ECLIPSE_S, ORBIT_S / 3600.0 + 1e-6));
    /* 92 x 1.02 - (16.435 - 13.07) W x 95.43 min = 93.84 - 5.35 */
    check("margin derived: capacity + 2 % reserve - one orbit's surplus = 88.5 Wh",
          close_to(battery_safety_margin_wh, want_margin(GEN_DELIVERED_W * SUNLIT_S / ORBIT_S, 0.02), 0.02) &&
              close_to(battery_safety_margin_wh, 88.49, 0.05) && strstr(log, "margin_src=derived") &&
              strstr(log, "orbit_surplus_wh=5.35"));
    check("phase 0 is eclipse exit, so t=0 is sunlit", sim.current_state == SUNLIT);
}

static void t_each_input_required(void)
{
    const char *required[] = {"DIPP_BATT_CAPACITY_WH", "DIPP_BATT_SOC0", "DIPP_BATT_BUS_W",
                              "DIPP_BATT_ORBIT_MIN", "DIPP_BATT_PHASE0", "DIPP_BATT_PROFILE",
                              "DIPP_BATT_BOARD_MODE", "DIPP_BATT_BOARD_IDLE_W",
                              "DIPP_BATT_BOARD_HOLD_S", "DIPP_BATT_RESERVE_FRAC"};
    int all = 1;
    for (size_t i = 0; i < sizeof(required) / sizeof(*required); i++)
    {
        platform();
        unsetenv(required[i]);
        all &= rejects(required[i]);
    }
    check("dropping any one required input refuses", all);
}

static void t_bad_values(void)
{
    struct { const char *var, *value; } cases[] = {
        {"DIPP_BATT_CAPACITY_WH", "0"},     {"DIPP_BATT_CAPACITY_WH", "abc"},
        {"DIPP_BATT_SOC0", "0"},            {"DIPP_BATT_SOC0", "1.5"},
        {"DIPP_BATT_BUS_W", "-1"},          {"DIPP_BATT_ORBIT_MIN", "95.43min"},
        {"DIPP_BATT_BOARD_MODE", "sometimes"}, {"DIPP_BATT_BOARD_IDLE_W", "-3"},
        {"DIPP_BATT_RESERVE_FRAC", "-0.01"}, {"DIPP_BATT_RESERVE_FRAC", "1.0"},
        {"DIPP_BATT_PROFILE", "/nonexistent.profile"},
    };
    int all = 1;
    for (size_t i = 0; i < sizeof(cases) / sizeof(*cases); i++)
    {
        platform();
        env_set(cases[i].var, cases[i].value);
        all &= rejects(cases[i].var);
    }
    check("out-of-range, non-numeric or unreadable values refuse", all);
}

static void t_bad_profiles(void)
{
    const char *bodies[] = {
        "0.0 1\n",                        /* one sample */
        "0.0 1\n0.5 2\n0.4 3\n",          /* not ascending */
        "0.0 1\n1.0 2\n",                 /* phase outside [0,1) */
        "0.0 1\n0.5 -2\n",                /* negative power */
        "0.0 1\nhello world\n0.5 2\n",    /* unparsable */
        "# only a comment\n",             /* no samples */
    };
    int all = 1;
    for (size_t i = 0; i < sizeof(bodies) / sizeof(*bodies); i++)
    {
        FILE *f = fopen(TEST_PROFILE, "w");
        fputs(bodies[i], f);
        fclose(f);
        platform();
        env_set("DIPP_BATT_PROFILE", TEST_PROFILE);
        all &= rejects(bodies[i]);
    }
    /* A line the 128-byte reader would split must be an error, not two lines. */
    FILE *f = fopen(TEST_PROFILE, "w");
    fprintf(f, "0.0 1\n# %0200d\n0.5 2\n", 0);
    fclose(f);
    platform();
    env_set("DIPP_BATT_PROFILE", TEST_PROFILE);
    all &= rejects("long line");
    check("malformed profiles refuse (no partial read, no rectangular fallback)", all);
    unlink(TEST_PROFILE);
}

static void t_margin_sources(void)
{
    platform();
    env_set("DIPP_BATT_MARGIN_WH", "50");
    CubeSatBatterySimulator sim;
    char *log;
    int rc = configure_quiet(&sim, &log);
    check("an explicit MARGIN_WH wins over RESERVE_FRAC, and says so",
          rc == 0 && close_to(battery_safety_margin_wh, 50.0, 1e-4) && strstr(log, "margin_src=env"));

    platform();
    env_set("DIPP_BATT_BUS_W", "20"); /* more than the arrays deliver on average */
    rc = configure_quiet(&sim, &log);
    check("a platform with no surplus configures, and says the payload can never run",
          rc == 0 && battery_safety_margin_wh > 92.0 && strstr(log, "BATTERY_MARGIN_ABOVE_CAPACITY"));

    platform();
    unsetenv("DIPP_BATT_RESERVE_FRAC");
    env_set("DIPP_BATT_MARGIN_WH", "50");
    check("MARGIN_WH alone is enough", configure_quiet(&sim, &log) == 0);

    platform();
    env_set("DIPP_BATT_BOARD_MODE", "off");
    unsetenv("DIPP_BATT_BOARD_IDLE_W");
    unsetenv("DIPP_BATT_BOARD_HOLD_S");
    check("board off needs no idle power or hold", configure_quiet(&sim, &log) == 0);
}

/* ------------------------------------------------------------- physics */

static void t_rect_physics(void)
{
    platform();
    env_set("DIPP_BATT_SOC0", "0.5"); /* away from both clamps for a full orbit */
    CubeSatBatterySimulator sim;
    char *log;
    configure_quiet(&sim, &log);
    double sum = 0;
    for (int k = 0; k < 36000; k++)
        sum += battery_simulator_p_gen(&sim, (k + 0.5) / 36000.0);
    check("rect profile delivers 16.435 W orbit average (17.3 x 0.95)", close_to(sum / 36000.0, 16.435, 0.01));
    check("phase wraps: 1.2 is 0.2",
          close_to(battery_simulator_p_gen(&sim, 1.2), battery_simulator_p_gen(&sim, 0.2), 1e-9));

    double wh0 = get_battery_level_wh();
    int steps = (int)floor(ORBIT_S);
    for (int i = 0; i < steps; i++)
        battery_simulator_step(&sim, 1.0);
    battery_simulator_step(&sim, ORBIT_S - steps);
    double want = (GEN_DELIVERED_W * SUNLIT_S - 13.07 * ORBIT_S) / 3600.0; /* +5.35 Wh */
    check("one orbit nets delivered generation minus bus load (+5.35 Wh)",
          close_to(get_battery_level_wh() - wh0, want, 0.01));
    check("simulated time tracks the steps 1:1 (no 100x acceleration)",
          close_to(sim.current_time_s, ORBIT_S, 1e-6));
    check("after a full orbit the state is sunlit again", sim.current_state == SUNLIT);
}

static void t_clamps(void)
{
    platform();
    env_set("DIPP_BATT_SOC0", "0.02");
    env_set("DIPP_BATT_BUS_W", "100");
    CubeSatBatterySimulator sim;
    char *log;
    configure_quiet(&sim, &log);
    int saved = capture_begin();
    for (int i = 0; i < 3600; i++)
        battery_simulator_step(&sim, 1.0);
    log = capture_end(saved);
    check("discharge clamps at empty (0 Wh), not at a chosen floor",
          close_to(get_battery_level_wh(), 0.0, 1e-6));
    check("reaching empty logs BATTERY_EMPTY exactly once",
          strstr(log, "BATTERY_EMPTY") && !strstr(strstr(log, "BATTERY_EMPTY") + 1, "BATTERY_EMPTY"));

    platform();
    env_set("DIPP_BATT_SOC0", "0.99");
    env_set("DIPP_BATT_BUS_W", "0");
    configure_quiet(&sim, &log);
    for (int i = 0; i < 3600; i++)
        battery_simulator_step(&sim, 1.0);
    check("charge clamps at capacity (92 Wh)", close_to(get_battery_level_wh(), 92.0, 1e-3));
}

static void t_profile_interpolation(void)
{
    FILE *f = fopen(TEST_PROFILE, "w");
    fprintf(f, "# phase p_gen_w\n0.0 0.0\n0.25 20.0\n0.5 0.0\n");
    fclose(f);
    platform();
    env_set("DIPP_BATT_PROFILE", TEST_PROFILE);
    CubeSatBatterySimulator sim;
    char *log;
    check("profile file loaded", configure_quiet(&sim, &log) == 0 && sim.n_profile == 3);
    check("linear interpolation between samples",
          close_to(battery_simulator_p_gen(&sim, 0.125), 10.0, 1e-6) &&
              close_to(battery_simulator_p_gen(&sim, 0.375), 10.0, 1e-6));
    check("wrap-around between the last and first sample",
          close_to(battery_simulator_p_gen(&sim, 0.75), 0.0, 1e-6) &&
              close_to(battery_simulator_p_gen(&sim, 0.999), 0.0, 1e-6));
    check("a profile below 0.5 W reads as eclipse", sim.current_state == ECLIPSE);
    env_set("DIPP_BATT_PHASE0", "0.25");
    configure_quiet(&sim, &log);
    check("phase0 selects where in the orbit t=0 sits",
          close_to(sim.p_gen_now_W, 20.0, 1e-6) && sim.current_state == SUNLIT);
    unlink(TEST_PROFILE);

    platform();
    env_set("DIPP_BATT_PHASE0", "0.8"); /* past the sunlit fraction 0.623 */
    configure_quiet(&sim, &log);
    check("phase0 0.8 starts the run in eclipse",
          sim.current_state == ECLIPSE && close_to(sim.p_gen_now_W, 0.0, 1e-9));
}

static void t_board_burst(void)
{
    platform();
    env_set("DIPP_BATT_SOC0", "0.5");
    env_set("DIPP_BATT_PROFILE", TEST_PROFILE); /* all-dark: isolate the loads */
    FILE *f = fopen(TEST_PROFILE, "w");
    fputs("0.0 0.0\n0.5 0.0\n", f);
    fclose(f);
    CubeSatBatterySimulator sim;
    char *log;
    configure_quiet(&sim, &log);
    int saved = capture_begin();
    double wh0 = get_battery_level_wh();
    for (int i = 0; i < 100; i++)
        battery_simulator_step(&sim, 1.0);
    int never = !sim.board_on && close_to(sim.board_wh, 0.0, 1e-12) &&
                close_to(wh0 - get_battery_level_wh(), 13.07 * 100 / 3600.0, 1e-3);

    sim.board_ever_active = 1; /* an ingest at t = 100 s */
    sim.board_last_active_s = sim.current_time_s;
    for (int i = 0; i < 200; i++)
        battery_simulator_step(&sim, 1.0);
    int hold = close_to(sim.board_wh, 3.30 * 60.0 / 3600.0, 1e-6) && !sim.board_on &&
               sim.board_power_ons == 1;

    double before = sim.board_wh;
    sim.board_busy = 1; /* an execution longer than the hold */
    sim.board_last_active_s = sim.current_time_s;
    for (int i = 0; i < 150; i++)
        battery_simulator_step(&sim, 1.0);
    sim.board_busy = 0;
    sim.board_last_active_s = sim.current_time_s;
    for (int i = 0; i < 100; i++)
        battery_simulator_step(&sim, 1.0);
    int busy = close_to(sim.board_wh - before, 3.30 * 210.0 / 3600.0, 1e-6) && sim.board_power_ons == 2;
    log = capture_end(saved);
    check("burst: a board that never had work draws nothing", never);
    check("burst: an ingest powers the board for exactly the 60 s hold", hold);
    check("burst: busy holds it on past the hold, then 60 s more", busy);
    check("burst: every switch is logged as BOARD_POWER",
          strstr(log, "BOARD_POWER") && strstr(log, "state=on") && strstr(log, "state=off"));

    env_set("DIPP_BATT_BOARD_MODE", "always");
    configure_quiet(&sim, &log);
    saved = capture_begin();
    for (int i = 0; i < 3600; i++)
        battery_simulator_step(&sim, 1.0);
    capture_end(saved);
    check("always: 3.30 W for every simulated second", close_to(sim.board_wh, 3.30, 1e-6));

    env_set("DIPP_BATT_BOARD_MODE", "off");
    configure_quiet(&sim, &log);
    sim.board_busy = 1;
    for (int i = 0; i < 100; i++)
        battery_simulator_step(&sim, 1.0);
    check("off: never charged, even while busy", close_to(sim.board_wh, 0.0, 1e-12));
    unlink(TEST_PROFILE);
}

static void t_digitised_profiles(void)
{
    if (access(BOL_PROFILE, R_OK) != 0 || access(EOL_PROFILE, R_OK) != 0)
    {
        check("digitised profiles present (run from the repository root)", 0);
        return;
    }
    const char *paths[] = {BOL_PROFILE, EOL_PROFILE};
    double want[] = {17.3 * 0.95, 17.3 * 27.2 / 29.3 * 0.95};
    for (int j = 0; j < 2; j++)
    {
        platform();
        env_set("DIPP_BATT_PROFILE", paths[j]);
        CubeSatBatterySimulator sim;
        char *log;
        configure_quiet(&sim, &log);
        double sum = 0;
        for (int k = 0; k < 36000; k++)
            sum += battery_simulator_p_gen(&sim, (k + 0.5) / 36000.0);
        char what[128];
        snprintf(what, sizeof(what), "%s delivers %.3f W", j ? "EOL" : "BOL", want[j]);
        check(what, close_to(sum / 36000.0, want[j], 0.01));
        snprintf(what, sizeof(what), "%s margin %.1f Wh follows the profile's surplus",
                 j ? "EOL" : "BOL", want_margin(want[j], 0.02));
        check(what, close_to(battery_safety_margin_wh, want_margin(sum / 36000.0, 0.02), 0.01));
        if (j == 0)
            check("its eclipse is 35.8 +- 0.3 min", close_to(sim.eclipse_s / 60.0, 35.8, 0.3));
    }
}

/* ------------------------------------------- the process-wide simulator */

static void t_use_before_init_aborts(void)
{
    /* Must run before this process calls battery_simulator_init. */
    pid_t pid = fork();
    if (pid == 0)
    {
        int devnull = open("/dev/null", O_WRONLY);
        dup2(devnull, STDERR_FILENO);
        battery_simulator_time_s();
        _exit(0);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    check("any use before battery_simulator_init aborts (a bug, not a state)",
          WIFSIGNALED(status) && WTERMSIG(status) == SIGABRT);
}

static void t_init_refuses(void)
{
    clear_env();
    int saved = capture_begin();
    int rc = battery_simulator_init();
    capture_end(saved);
    check("battery_simulator_init refuses an unconfigured environment", rc != 0);
}

static void t_advance_matches_living_through_it(void)
{
    platform();
    env_set("DIPP_BATT_SOC0", "0.5");
    env_set("DIPP_BATT_PHASE0", "0.5"); /* the gap crosses the eclipse boundary */
    int saved = capture_begin();
    int rc = battery_simulator_init();
    battery_simulator_advance(3000.0);
    char *log = capture_end(saved);
    check("battery_simulator_init accepts a complete platform", rc == 0);

    double want = integrate_rect(46.0, 0.0, 3000.0, 1.0, GEN_DELIVERED_W, 13.07, ORBIT_S, SUNLIT_S,
                                 0.5, 92.0);
    check("advance(3000) equals a host-side integration of the same 3000 s",
          close_to(get_battery_level_wh(), want, 0.01));
    check("simulated time advanced by exactly the gap", close_to(battery_simulator_time_s(), 3000.0, 1e-6));
    check("phase after the gap is t/orbit + phase0, wrapped",
          close_to(battery_simulator_phase(), fmod(3000.0 / ORBIT_S + 0.5, 1.0), 1e-6));
    check("BATTERY_FF line carries the advance for the trace analysis",
          strstr(log, "BATTERY_FF") && strstr(log, "adv_s=3000.0") && strstr(log, "sim_t_after_s=3000.00"));

    /* Chunks, as run.sh advances, must land where one jump does. */
    double after_first = get_battery_level_wh();
    saved = capture_begin();
    for (int i = 0; i < 10; i++)
        battery_simulator_advance(60.0);
    capture_end(saved);
    double one_jump = integrate_rect(after_first, 3000.0, 600.0, 1.0, GEN_DELIVERED_W, 13.07, ORBIT_S,
                                     SUNLIT_S, 0.5, 92.0);
    check("ten 60 s chunks equal one 600 s advance", close_to(get_battery_level_wh(), one_jump, 0.01));
    check("a zero or negative advance is a no-op",
          (battery_simulator_advance(0.0), battery_simulator_advance(-5.0),
           close_to(battery_simulator_time_s(), 3600.0, 1e-6)));
}

static void t_set_wh_and_load(void)
{
    int saved = capture_begin();
    battery_simulator_set_wh(1000.0f);
    double high = get_battery_level_wh();
    battery_simulator_set_wh(-5.0f);
    double low = get_battery_level_wh();
    char *log = capture_end(saved);
    check("set-wh clamps to [0, capacity]", close_to(high, 92.0, 1e-3) && close_to(low, 0.0, 1e-6));
    check("BATTERY_SET is logged", strstr(log, "BATTERY_SET") != NULL);

    saved = capture_begin();
    battery_simulator_set_wh(50.0f);
    put_load_on_battery(1000000.0f); /* 1 Wh in uWh */
    double after = get_battery_level_wh();
    put_load_on_battery(1e9f); /* far more than is there */
    log = capture_end(saved);
    check("put_load_on_battery debits microwatt-hours", close_to(after, 49.0, 1e-3));
    check("an execution debit that empties the battery logs BATTERY_EMPTY",
          close_to(get_battery_level_wh(), 0.0, 1e-6) && strstr(log, "cause=execution_debit"));
}

int main(void)
{
    printf("battery / power model\n");
    t_use_before_init_aborts();
    t_nothing_set();
    t_complete_platform();
    t_each_input_required();
    t_bad_values();
    t_bad_profiles();
    t_margin_sources();
    t_rect_physics();
    t_clamps();
    t_profile_interpolation();
    t_board_burst();
    t_digitised_profiles();
    t_init_refuses();
    t_advance_matches_living_through_it();
    t_set_wh_and_load();
    unlink(RECT_PROFILE);
    printf("%s (%d failures)\n", failures ? "FAILED" : "OK", failures);
    return failures ? 1 : 0;
}
