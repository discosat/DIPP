#ifndef DIPP_CONFIG_H
#define DIPP_CONFIG_H

#include <stdlib.h>
#include <stdint.h>

#define MAX_MODULES 20
#define MAX_PIPELINES 6

#define PIPELINE_PARAMID_OFFSET 10
#define MODULE_PARAMID_OFFSET 30

/* Structs for storing module and pipeline configurations */
typedef struct Module
{
    char *module_name;
    void *module_function;
    // default effort to be set in case there is only a single effort level
    int default_effort_param_id;
    // Distinct effort levels for the module,
    // only set these in case there are multiple effort levels
    int low_effort_param_id;
    int medium_effort_param_id;
    int high_effort_param_id;
} Module;

typedef struct Pipeline
{
    int pipeline_id;
    Module modules[MAX_MODULES];
    size_t num_modules;
} Pipeline;

/* Local structures for saving module parameter configurations (translated from Protobuf) */
typedef enum
{
    NOT_SET = 0,
    BOOL_VALUE = 2,
    INT_VALUE = 3,
    FLOAT_VALUE = 4,
    STRING_VALUE = 5
} ModuleParameter__ValueCase;

typedef struct ModuleParameter
{
    char *key;
    ModuleParameter__ValueCase value_case;
    union
    {
        int bool_value;
        int int_value;
        float float_value;
        char *string_value;
    };
} ModuleParameter;

typedef struct ModuleParameterList
{
    size_t n_parameters;
    uint32_t hash;
    uint32_t latency_cost;
    uint32_t energy_cost;
    ModuleParameter **parameters;
} ModuleParameterList;

/* Stashed pipelines and module parameters */
extern Pipeline pipelines[];
extern ModuleParameterList module_parameter_lists[];

/* Preload all configurations if not done yet */
void setup_cache_if_needed();
void invalidate_cache();

#endif
