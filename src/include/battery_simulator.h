#ifndef DIPP_BATTERY_H
#define DIPP_BATTERY_H

#include <stdint.h>
#include "dipp_paramids.h"

#define BATTERY_SAFETY_MARGIN_WH 64.4f    // Safety margin in Watt-hours (70% of 92 Wh total capacity)
#define SIMULATION_UPDATE_PERIOD_US 10000 // 10000 // Update every 10 milliseconds
#define SIMULATION_STEP_US 1000000        // One step represents one second in simulation
#define SIMULATION_STEPS_PER_UPDATE (SIMULATION_STEP_US / SIMULATION_UPDATE_PERIOD_US)

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
    int time_step_s;
    int current_time_s;
    int orbit_period_s;
    int sunlit_duration_s;

    // --- State ---
    StateEnum current_state; // "SUNLIT" or "ECLIPSE"

} CubeSatBatterySimulator;

void simulate_battery();

float get_battery_level_wh();

void put_load_on_battery(float load_uWh);

#endif // DIPP_BATTERY_H