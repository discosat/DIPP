#ifndef DIPP_CONFIG_PARAM_H
#define DIPP_CONFIG_PARAM_H

#include <param/param.h>
#include "dipp_paramids.h"
#include "vmem_storage.h"

#define DATA_PARAM_SIZE 188

/* Define module configuration parameters */
void setup_module_config(param_t *param, int index);
PARAM_DEFINE_STATIC_VMEM(PARAMID_MODULE_PARAM_1,  module_param_1,  PARAM_TYPE_DATA, DATA_PARAM_SIZE, 0, PM_CONF, setup_module_config, NULL, storage, VMEM_CONF_MODULE_1,  NULL);

/* Define pipeline configuration parameters */
void setup_pipeline(param_t *param, int index);
PARAM_DEFINE_STATIC_VMEM(PARAMID_PIPELINE_CONFIG_1, pipeline_config_1, PARAM_TYPE_DATA, DATA_PARAM_SIZE, 0, PM_CONF, setup_pipeline, NULL, storage, VMEM_CONF_PIPELINE_1, NULL);

param_t *module_config_params[] = {
    &module_param_1
};

param_t *pipeline_config_params[] = {
    &pipeline_config_1
};

#endif