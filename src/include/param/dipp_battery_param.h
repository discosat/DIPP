#ifndef DIPP_BATTERY_PARAM_H
#define DIPP_BATTERY_PARAM_H

#include <param/param.h>
#include "dipp_paramids.h"

// /* Define a pipeline_run parameter */
static float _battery_level = 0.0f;
PARAM_DEFINE_STATIC_RAM(PARAMID_BATTERY_LEVEL, battery_level, PARAM_TYPE_FLOAT, -1, 0, PM_CONF, NULL, NULL, &_battery_level, "Current battery level in Wh.");
#endif