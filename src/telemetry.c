#include "telemetry.h"

#include <csp/csp.h>
#include <param/param.h>
#include <param/param_client.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

// Static storage for remote energy reading
static uint8_t _measurement_flag = 0;
static float _remote_energy = 0.0f;

// Define remote parameter to access mock energy readings
PARAM_DEFINE_REMOTE_DYNAMIC(
    PARAMID_MEASUREMENT_FLAG,               // ID
    measurement_flag,                       // Name
    ENERGY_NODE_ADDR,                       // Node address
    PARAM_TYPE_UINT8,                       // Type
    -1,                                     // Array count (-1 for single value)
    0,                                      // Array step (0 for single value)
    PM_REMOTE,                              // Flags
    &_measurement_flag,                     // Physical address points to our static variable
    "Remote energy sensor measurement flag" // Doc string
);

PARAM_DEFINE_REMOTE_DYNAMIC(
    PARAMID_LAST_RECORDED_ENERGY,  // ID
    remote_energy,                 // Name
    ENERGY_NODE_ADDR,              // Node address
    PARAM_TYPE_FLOAT,              // Type
    -1,                            // Array count (-1 for single value)
    0,                             // Array step (0 for single value)
    PM_REMOTE,                     // Flags
    &_remote_energy,               // Physical address points to our static variable
    "Remote energy sensor reading" // Doc string
);

void initialize_telemetry()
{
    // Add the mock energy parameter to the parameter list
    // param_list_add(&measurement_flag);
    // param_list_add(&remote_energy);
    return;
}

int trigger_energy_measurement(uint8_t flag)
{
    uint8_t temp_val = flag;
    int ret;

    ret = param_push_single(&measurement_flag, -1, &temp_val, 1, ENERGY_NODE_ADDR, 500, 2, 1);
    fprintf(stderr, "param_push_single idx=-1 cnt=1 ret=%d\n", ret);
    if (ret >= 0)
        return 0;

    // Try the original call (count=0) to confirm behavior
    ret = param_push_single(&measurement_flag, 0, &temp_val, 0, ENERGY_NODE_ADDR, 500, 2, 1);
    fprintf(stderr, "param_push_single idx=0 cnt=0 ret=%d\n", ret);
    if (ret >= 0)
        return 0;

    fprintf(stderr, "Failed to push start measurement flag (all variants failed)\n");
    return -1; // Success
}

int start_energy_measurement()
{
    // Set measurement flag to 1 to start measurement
    // return trigger_energy_measurement(1);
    return 0;
}

float get_energy_reading()
{

    // float old_energy = _remote_energy;

    // uint8_t temp_val = 0;

    // if (param_push_single(&measurement_flag, -1, &temp_val, 1, ENERGY_NODE_ADDR, 500, 2, 1) != 0)
    // {
    //     fprintf(stderr, "Failed to push start measurement flag\n");
    //     // return -1.0f; // Indicate failure
    // }

    // trigger_energy_measurement(0); // Reset measurement flag

    // // wait till new reading is available
    // int max_retries = 10;
    // int retries = 0;
    // while (retries < max_retries)
    // {
    //     if (param_pull_single(&remote_energy, -1, CSP_PRIO_NORM, 1, ENERGY_NODE_ADDR, 5000, 2) != 0)
    //     {
    //         fprintf(stderr, "Failed to pull energy reading\n");
    //         // return -1.0f; // Return 0 if the pull fails
    //     }
    //     if (_remote_energy != old_energy)
    //     {
    //         break; // New reading available
    //     }
    //     retries++;
    // }
    // return _remote_energy;

    return rand() % 1000 / 10.0f; // Return a mock energy reading between 0.0 and 99.9
}