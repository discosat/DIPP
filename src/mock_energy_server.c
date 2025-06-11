#include <stdio.h>
#include "mock_energy_server.h"
#include <stdlib.h>
#include <unistd.h>
#include <time.h>
#include <pthread.h>
#include <csp/csp.h>
#include <csp/csp_iflist.h>
#include <csp/csp_rtable.h>
#include <csp/drivers/can_socketcan.h>
#include <param/param.h>
#include <param/param_server.h>

// Mock energy sensor parameter value
static int32_t mock_energy_value = 0;

// Parameter definition for mock energy sensor
PARAM_DEFINE_STATIC_RAM(MOCK_ENERGY_NJ_ID, mock_energy_nj, PARAM_TYPE_UINT32, -1, 0,
                        PM_CSP, NULL, "nJ", &mock_energy_value,
                        "Mock energy consumption in nanojoules");

uint32_t _serial0;

#define PARAMID_SERIAL0 31

PARAM_DEFINE_STATIC_RAM(PARAMID_SERIAL0, serial0, PARAM_TYPE_INT32, -1, 0, PM_HWREG, NULL, "", &_serial0, NULL);

void serial_init(void)
{
    _serial0 = rand();
}

uint32_t serial_get(void)
{
    return _serial0;
}

static int32_t get_mock_energy_reading(void)
{
    // Generate random value between 1000 and 10000 nJ
    return 1000 + (rand() % 9000);
}

void *router_task(void *param)
{
    while (1)
    {
        csp_route_work();
    }
    return NULL;
}

void *energy_update_task(void *param)
{
    while (1)
    {
        mock_energy_value = get_mock_energy_reading();
        sleep(1);
    }
    return NULL;
}

static void iface_init(void)
{
    csp_iface_t *iface = NULL;

    // Initialize CAN interface using socketcan
    int error = csp_can_socketcan_open_and_add_interface("vcan0", "CAN", MOCK_ENERGY_NODE_ADDR, 1000000, 0, &iface);
    if (error != CSP_ERR_NONE)
    {
        csp_print("Failed to add CAN interface [vcan0], error: %d\n", error);
        exit(1);
    }

    // Set interface parameters
    iface->name = "CAN";
    iface->netmask = 8;

    // Add interface to CSP
    csp_rtable_set(0, 0, iface, CSP_NO_VIA_ADDRESS);
    csp_iflist_add(iface);
}

int main(void)
{
    printf("Mock Energy CSP Server starting...\n");

    // Initialize random seed
    srand(time(NULL));

    // Initialize CSP
    csp_conf.hostname = "mock-energy";
    csp_init();

    // Initialize interface
    iface_init();

    // Print CSP configuration
    csp_print("Connection table\r\n");
    csp_conn_print_table();
    csp_print("Interfaces\r\n");
    csp_iflist_print();
    csp_print("Route table\r\n");
    csp_rtable_print();

    // Bind callbacks
    csp_bind_callback(param_serve, PARAM_PORT_SERVER);

    // Create router thread
    pthread_t router_handle;
    pthread_create(&router_handle, NULL, &router_task, NULL);

    // Create energy update thread
    pthread_t energy_handle;
    pthread_create(&energy_handle, NULL, &energy_update_task, NULL);

    printf("Mock Energy CSP Server running on CAN bus...\n");

    // Main loop
    while (1)
    {
        sleep(10);
    }

    return 0;
}