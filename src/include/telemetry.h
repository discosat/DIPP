#ifndef DIPP_TELEMETRY_H
#define DIPP_TELEMETRY_H

#include <stdint.h>

#define PARAMID_MEASUREMENT_FLAG 903
#define PARAMID_LAST_RECORDED_ENERGY 905
#define ENERGY_NODE_ADDR 5412

/*
Initialize the telemetry system by setting up remote parameters for energy measurement.
*/
void initialize_telemetry();

/*
Start the energy measurement on the remote node by setting the measurement flag.
Returns 0 on success, -1 on failure.
*/
int start_energy_measurement();

/*
Stop the energy measurement on the remote node by clearing the measurement flag.
Waits for the energy reading to change and returns the latest reading.
Returns the energy reading in joules, or -1.0f on failure.
*/
float get_energy_reading();

void telemetry_run_param_mode_tests(void);

#endif // DIPP_TELEMETRY_H