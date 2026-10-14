#ifndef DIPP_BATTERY_H
#define DIPP_BATTERY_H

#include <stdint.h>
#include "dipp_paramids.h"

/* Battery / power model (Stage 5a).
 *
 * One integrator, two callers: the 10 ms simulator thread, which advances
 * simulated time 1:1 with wall time, and battery_simulator_advance(), which
 * integrates a stated number of simulated seconds at once. The latter is how
 * a trace replay compresses the hours between imaging bursts: the daemon is
 * idle across a gap, so nothing but the battery needs the time to pass, and
 * the charge model is the same either way.
 *
 * Nothing here has a default. Every input is required from the environment
 * and battery_simulator_init() refuses to start -- listing everything that is
 * missing or invalid -- rather than substitute a value. A platform is one
 * preset file (experiments/platforms/<name>.env, loaded by run.sh); the
 * run's initial conditions come from run.sh's --soc0 and --phase0.
 *
 *   DIPP_BATT_CAPACITY_WH   battery capacity
 *   DIPP_BATT_BUS_W         platform load, always on, excluding the board
 *   DIPP_BATT_ORBIT_MIN     orbit period
 *   DIPP_BATT_PROFILE       generation profile: "phase p_gen_w" lines, phase
 *                           in [0,1) ascending, W DELIVERED TO THE BUS (after
 *                           the power point tracker), linear with wrap; phase
 *                           0 is eclipse exit. tools/make_power_profile.py
 *                           builds it -- a digitised curve or an exact-edge
 *                           rectangular one.
 *   DIPP_BATT_SOC0          initial state of charge, (0, 1]
 *   DIPP_BATT_PHASE0        orbit phase at simulated t = 0
 *   DIPP_BATT_BOARD_MODE    burst | always | off (below)
 *   DIPP_BATT_BOARD_IDLE_W  board draw while powered   } unless mode = off
 *   DIPP_BATT_BOARD_HOLD_S  burst: powered this long   }
 *                           after the last activity
 *   DIPP_BATT_SURVIVAL_ORBITS  N: orbits the platform alone must be able to
 *                           ride out with no generation (margin, below)
 *   or DIPP_BATT_MARGIN_WH  the margin itself -- exactly one of the two
 *
 * The battery is clamped to [0, capacity] -- the physical limits, not a
 * chosen floor. Reaching empty logs BATTERY_EMPTY: the scenario has broken
 * the platform, and the analysis must say so rather than read on.
 *
 * The processing board. Its standing draw is in no platform budget the
 * per-execution debits cover (they are idle-corrected), so it is charged
 * here. BOARD_MODE:
 *   burst  powered while it has work -- an ingest, a module executing -- and
 *          for BOARD_HOLD_S after the last of it, then switched off, as a
 *          PDU and flight planner allow. A board that cannot run anything
 *          (energy-halted) goes dark too.
 *   always an always-on board, the counterfactual.
 *   off    not charged (the pre-Stage-5a behaviour).
 *
 * The payload safety margin, when derived (decided 2026-10-03):
 *   margin = SURVIVAL_ORBITS * BUS_W * orbit period
 * The payload may never take the battery below what the platform alone needs
 * to survive N orbits without any generation (tumbling, safe mode). Every
 * term but N comes from the platform; N is the stated policy (2 for DISCO-II:
 * 41.6 Wh, 45 % of 92 Wh). It replaces "one orbit's surplus below full + 2 %"
 * (88.5 Wh, 96.2 %), which sat above the battery's own post-eclipse level
 * and halted the payload after every eclipse whatever it had spent. Battery
 * wear is not a floor here: the depth of discharge reached per orbit is a
 * measured result. A margin at or above capacity is logged as
 * BATTERY_MARGIN_ABOVE_CAPACITY -- the payload never runs.
 *
 * Simulated time advances 1:1 with wall-clock time. Previously one 10 ms
 * update advanced the simulation by a full second, a 100x acceleration, and
 * every energy figure was multiplied by SIMULATION_STEPS_PER_UPDATE to
 * compensate. That kept power self-consistent but compressed throughput
 * against orbital dynamics by 100x. Goodput per orbit is exactly what the
 * paper claims to measure, so the acceleration has been removed.
 *
 * The DISCO-II values and where each comes from: experiments/platforms/ and
 * analysis/power-profile/README.md. */

#define SIMULATION_UPDATE_PERIOD_US 10000 // Wall-clock period between simulator steps (10 ms)
#define SIMULATION_STEP_S (SIMULATION_UPDATE_PERIOD_US / 1000000.0)
#define BATTERY_LOG_PERIOD_S 10.0 /* one BATTERY line per this much simulated time */

/* Energy the scheduler must leave in the battery. Was the compile-time
   constant BATTERY_SAFETY_MARGIN_WH 64.4; now set by battery_simulator_init
   (derived or DIPP_BATT_MARGIN_WH), which runs before any reader exists. */
extern float battery_safety_margin_wh;

typedef enum
{
    SUNLIT,
    ECLIPSE
} StateEnum;

typedef enum
{
    BOARD_BURST = 0,
    BOARD_ALWAYS = 1,
    BOARD_OFF = 2,
} BoardMode;

typedef struct
{
    double total_capacity_Wh;
    double soc;
    double constant_load_W; /* platform load, always on, excluding the board */

    double current_time_s; /* simulated seconds since start */
    double orbit_period_s;
    double phase0; /* orbit phase at simulated t = 0, 0 = eclipse exit */

    /* Generation profile, W delivered to the bus. */
    int n_profile;
    double *profile_phase;
    double *profile_w;

    StateEnum current_state;
    double p_gen_now_W;
    int empty_logged;

    /* Processing board (see above). */
    BoardMode board_mode;
    double board_idle_W;
    double board_hold_s;
    double board_last_active_s; /* simulated time of the last activity */
    int board_busy;             /* a module is executing right now */
    int board_ever_active;
    int board_on;
    long board_power_ons;
    double board_wh; /* cumulative idle energy charged to the board */

    double eclipse_s; /* of the profile, logged */
} CubeSatBatterySimulator;

/* Configure the process-wide simulator from the environment. Call once, in
   main(), before any thread that reads the battery starts. Non-zero means it
   is not configured and the daemon must not run; BATTERY_CONFIG_ERROR lines
   say why. */
int battery_simulator_init(void);

void simulate_battery(void);

float get_battery_level_wh(void);
void put_load_on_battery(float load_uWh);

/* Integrate `seconds` of simulated time now (1 s steps). Logs BATTERY_FF. */
void battery_simulator_advance(double seconds);
/* Set the stored energy directly (clamped to [0, capacity]). */
void battery_simulator_set_wh(float wh);
/* Generation delivered to the bus at an orbit phase (wrapped). */
double battery_simulator_p_gen(const CubeSatBatterySimulator *sim, double phase);
/* Current simulated time and orbit phase. */
double battery_simulator_time_s(void);
double battery_simulator_phase(void);

/* What the scheduler's energy-timed check reads (sched_policy.h, SchedEnergy):
   battery now, floor, the board's idle power and mode, simulated time, the
   payload's cumulative energy (board idle + module debits) and the orbit
   period. On a satellite these come from EPS telemetry and the platform's
   configuration. Returns 0, or -1 before the simulator is configured. */
int battery_simulator_energy_state(double *w_wh, double *floor_wh, double *idle_w, double *hold_s,
                                   int *always, double *t_s, double *payload_wh, double *orbit_s);

/* Processing-board activity, from the daemon: an ingest, or a module
   starting/finishing. busy=1 keeps the board powered for the whole of an
   execution however long it runs; busy=0 starts the hold. */
void battery_simulator_board_activity(void);
void battery_simulator_board_busy(int busy);

/* Host-testable core. configure returns 0, or -1 with BATTERY_CONFIG_ERROR
   lines; on -1 the simulator must not be stepped. */
int battery_simulator_configure(CubeSatBatterySimulator *sim);
void battery_simulator_step(CubeSatBatterySimulator *sim, double dt_s);

#endif // DIPP_BATTERY_H
