#include <param/param.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include "mock_energy_server.h"

// Static storage for remote energy reading
static uint32_t remote_energy_value = 0;

// Define remote parameter to access mock energy readings
PARAM_DEFINE_REMOTE_DYNAMIC(
    MOCK_ENERGY_NJ_ID,             // ID
    mock_energy_nj,                // Name
    MOCK_ENERGY_NODE_ADDR,         // Node address
    PARAM_TYPE_UINT32,             // Type
    -1,                            // Array count (-1 for single value)
    0,                             // Array step (0 for single value)
    PM_CSP,                        // Flags
    &remote_energy_value,          // Physical address points to our static variable
    "Remote energy sensor reading" // Doc string
);

void initialize_telemetry()
{
    // Initialize the parameter system
    param_init();

    // Add the mock energy parameter to the parameter list
    param_list_add(&mock_energy_nj);
}

uint32_t get_energy_reading()
{

    if (param_pull_single(&mock_energy_nj, 0, CSP_PRIO_HIGH, 0, MOCK_ENERGY_NODE_ADDR, 500, 2) != 0)
    {
        fprintf(stderr, "Failed to pull start energy reading\n");
        return 0; // Return 0 if the pull fails
    }
    else
    {
        return param_get_uint32(&mock_energy_nj);
    }
}
