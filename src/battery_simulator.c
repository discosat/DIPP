#include "battery_simulator.h"

#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
#include <math.h>

#include "dipp_battery_param.h"

/**
 * @brief Initializes the battery simulator structure with starting values.
 *
 * @param sim Pointer to the simulator structure to be initialized.
 * @param total_capacity_Wh Total energy capacity (Watt-hours).
 * @param initial_soc Starting state of charge (0.0 to 1.0).
 * @param constant_load_W The baseline power draw (Watts).
 * @param power_generation_W Power generated when in sunlight (Watts).
 * @param orbit_period_min The total orbital period (minutes).
 * @param eclipse_duration_min The time spent in shadow (minutes).
 * @param time_step_s The simulation step size (seconds).
 * @param min_soc_limit The minimum allowed SoC (e.g., 0.2).
 * @param max_soc_limit The maximum allowed SoC (e.g., 1.0).
 */
void simulator_init(CubeSatBatterySimulator *sim,
                    double total_capacity_Wh,
                    double initial_soc,
                    double constant_load_W,
                    double power_generation_W,
                    double orbit_period_min,
                    double eclipse_duration_min,
                    int time_step_s,
                    double min_soc_limit,
                    double max_soc_limit)
{
    // --- Battery ---
    sim->total_capacity_Wh = total_capacity_Wh;
    sim->min_capacity_Wh = total_capacity_Wh * min_soc_limit;
    sim->max_capacity_Wh = total_capacity_Wh * max_soc_limit;
    // sim->current_capacity_Wh = total_capacity_Wh * initial_soc;
    sim->soc = initial_soc;

    // --- Power ---
    sim->power_generation_W = power_generation_W;
    sim->constant_load_W = constant_load_W;

    // --- Time & Orbit ---
    sim->time_step_s = time_step_s;
    sim->current_time_s = 0;
    sim->orbit_period_s = (int)(orbit_period_min * 60);
    sim->sunlit_duration_s = (int)((orbit_period_min - eclipse_duration_min) * 60);

    // --- State ---
    sim->current_state = SUNLIT;

    float initial_capacity = total_capacity_Wh * initial_soc;
    param_set_float(&battery_level, initial_capacity);
}

/**
 * @brief Advances the simulation by one time step and writes results to a file.
 *
 * @param sim Pointer to the simulator structure to update.
 * @param log_file File pointer to the open CSV log.
 */
void simulator_step(CubeSatBatterySimulator *sim)
{
    // 1. Determine orbital state (SUNLIT or ECLIPSE)
    int time_in_orbit_s = sim->current_time_s % sim->orbit_period_s;
    double power_in_W;

    if (time_in_orbit_s >= 0 && time_in_orbit_s < sim->sunlit_duration_s)
    {
        sim->current_state = SUNLIT;
        power_in_W = sim->power_generation_W;
    }
    else
    {
        sim->current_state = ECLIPSE;
        power_in_W = 0.0;
    }

    // 2. Calculate total power draw (only constant load)
    double power_out_W = sim->constant_load_W;

    // 3. Calculate net power
    double net_power_W = power_in_W - power_out_W;

    // 4. Calculate energy change for this time step (in Watt-hours)
    double energy_change_Wh = (net_power_W * sim->time_step_s) / 3600.0;

    // 5. Update battery capacity
    float battery_level_wh = get_battery_level_wh();
    float new_capacity = battery_level_wh + energy_change_Wh;
    new_capacity = fmax(
        sim->min_capacity_Wh,
        fmin(sim->max_capacity_Wh, new_capacity));

    param_set_float(&battery_level, new_capacity);
    // sim->current_capacity_Wh += energy_change_Wh;

    // 6. Update SoC
    sim->soc = new_capacity / sim->total_capacity_Wh;

    // 7. Advance time
    sim->current_time_s += sim->time_step_s;
}

void simulate_battery()
{
    const int TIME_STEP_S = SIMULATION_STEP_US / 1000000;
    const double ORBIT_PERIOD_MIN = 98.0;

    CubeSatBatterySimulator sim;
    simulator_init(
        &sim,
        92.0, // total_capacity_Wh
        0.7,  // initial_soc
        16.5, // constant_load_W
        26.0, // power_generation_W
        ORBIT_PERIOD_MIN,
        33.0, // eclipse_duration_min
        TIME_STEP_S,
        0.2, // min_soc_limit
        1.0  // max_soc_limit
    );

    while (1)
    {
        simulator_step(&sim);
        usleep(SIMULATION_UPDATE_PERIOD_US);
    }
}

float get_battery_level_wh()
{
    return param_get_float(&battery_level);
}

void put_load_on_battery(float load_uWh)
{
    float battery_level_wh = get_battery_level_wh();
    float new_capacity = fmax(0.0, battery_level_wh - (load_uWh / 1000000.0));
    param_set_float(&battery_level, new_capacity);
}