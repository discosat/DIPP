#ifndef DIPP_BATTERY_H
#define DIPP_BATTERY_H

#include <stdint.h>
#include "dipp_paramids.h"

#define BATTERY_SAFETY_MARGIN_WH 64.4f    // Safety margin in Watt-hours (70% of 92 Wh total capacity)
#define SIMULATION_UPDATE_PERIOD_US 10000 // Wall-clock period between simulator steps (10 ms)

/* Simulated time advances 1:1 with wall-clock time.
 *
 * Previously one 10 ms update advanced the simulation by a full second, a 100x
 * acceleration, and every energy figure was multiplied by SIMULATION_STEPS_PER_UPDATE
 * to compensate. That kept power self-consistent but compressed throughput against
 * orbital dynamics by 100x: a pipeline that occupies 0.155% of an orbit appeared to
 * occupy 15.5% of one. Goodput per orbit is exactly what the paper claims to measure,
 * so the acceleration has been removed rather than compensated for. */
#define SIMULATION_STEP_S (SIMULATION_UPDATE_PERIOD_US / 1000000.0)

typedef enum
{
    SUNLIT,
    ECLIPSE
} StateEnum;

typedef struct
{
    // --- Battery Parameters ---
    double total_capacity_Wh;
    double min_capacity_Wh;
    double max_capacity_Wh;
    // double current_capacity_Wh;
    double soc; // State of Charge (0.0 to 1.0)

    // --- Power Parameters ---
    double power_generation_W;
    double constant_load_W;

    // --- Time & Orbit Parameters ---
    double time_step_s;
    double current_time_s;
    int orbit_period_s;
    int sunlit_duration_s;

    // --- State ---
    StateEnum current_state; // "SUNLIT" or "ECLIPSE"

} CubeSatBatterySimulator;

void simulate_battery();

float get_battery_level_wh();

void put_load_on_battery(float load_uWh);

#endif // DIPP_BATTERY_H