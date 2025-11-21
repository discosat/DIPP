#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <param/param.h>
#include <brotli/decode.h>
#include "dipp_error.h"
#include "dipp_config.h"
#include "dipp_config_param.h"
#include "dipp_paramids.h"
#include "vmem_storage.h"
#include "module_config.pb-c.h"
#include "pipeline_config.pb-c.h"
#include "murmur_hash.h"
#include "utils/minitrace.h"

Pipeline pipelines[MAX_PIPELINES];
ModuleParameterList module_parameter_lists[MAX_MODULES];

static int is_setup = 0;

int is_buffer_empty(uint8_t *buffer, size_t size)
{
    for (size_t i = 0; i < size; i++)
    {
        if (buffer[i] != 0)
        {
            return 0; // Buffer contains non-zero values
        }
    }
    return 1; // Buffer contains only 0 values
}

size_t get_param_buffer(uint8_t **out, param_t *param)
{
    uint8_t buf[DATA_PARAM_SIZE];
    param_get_data(param, buf, DATA_PARAM_SIZE);

    if (is_buffer_empty(buf, DATA_PARAM_SIZE))
        return 0;

    size_t decoded_size = DATA_PARAM_SIZE;
    uint8_t decoded_buffer[decoded_size];
    if (BrotliDecoderDecompress((size_t)buf[0], buf + 1, &decoded_size, decoded_buffer) != BROTLI_DECODER_RESULT_SUCCESS)
    {
        set_error_param(INTERNAL_BROTLI_DECODE);
        return 0;
    }

    *out = malloc(decoded_size * sizeof(uint8_t));
    if (!*out)
    {
        set_error_param(MEMORY_MALLOC);
        return 0;
    }

    // Copy the data from the original buffer to the new buffer
    for (size_t i = 0; i < decoded_size; i++)
    {
        (*out)[i] = decoded_buffer[i];
    }

    return decoded_size;
}

// Function to load a module and parameter from a configuration file
void *load_module(char *moduleName)
{
    char filename[256]; // Adjust the buffer size as needed
    snprintf(filename, sizeof(filename), "/usr/share/pipeline/%s.so", moduleName);

    printf("Loading module from %s\r\n", filename);

    // Load the external library dynamically
    void *handle = dlopen(filename, RTLD_LAZY);
    if (handle == NULL)
    {
        printf("Error loading module: %s\r\n", dlerror());
        set_error_param(INTERNAL_SO_NOT_FOUND);
        return NULL;
    }

    // Get a function pointer to the external function
    void *functionPointer = dlsym(handle, "run");
    if (functionPointer == NULL)
    {
        set_error_param(INTERNAL_RUN_NOT_FOUND);
        dlclose(handle);
        return NULL;
    }

    return functionPointer;
}

void setup_pipeline(param_t *param, int index)
{
    uint8_t *buffer = NULL;
    size_t buf_size = get_param_buffer(&buffer, param);

    PipelineDefinition *pdef = pipeline_definition__unpack(NULL, buf_size, buffer);
    free(buffer);
    buffer = NULL;

    if (!pdef)
    {
        return; // Skip this pipeline if unpacking fails
    }

    // print the pipeline definition for debugging
    printf("Pipeline Definition: ID=%d, Num Modules=%zu\n", param->id - PIPELINE_PARAMID_OFFSET, pdef->n_modules);
    for (size_t i = 0; i < pdef->n_modules; i++)
    {
        ModuleDefinition *mdef = pdef->modules[i];
        printf("  Module %zu: Name=%s, Num Implementations=%zu\n", i, mdef->name, mdef->n_implementations);
        for (size_t j = 0; j < mdef->n_implementations; j++)
        {
            Implementation *impl = mdef->implementations[j];
            printf("    Implementation %zu: Param ID=%d, Effort Level=%d\n", j, impl->param_id, impl->effort_level);
        }
    }

    int pipeline_id = param->id - PIPELINE_PARAMID_OFFSET;
    pipelines[pipeline_id].pipeline_id = pipeline_id + 1;
    pipelines[pipeline_id].num_modules = pdef->n_modules;

    printf("Setting up pipeline ID %d with %zu modules\r\n", pipeline_id + 1, pdef->n_modules);

    for (size_t module_idx = 0; module_idx < pdef->n_modules; module_idx++)
    {
        ModuleDefinition *mdef = pdef->modules[module_idx];

        pipelines[pipeline_id].modules[module_idx].module_name = strdup(mdef->name);
        pipelines[pipeline_id].modules[module_idx].module_function = load_module(mdef->name);

        // set the default param id (not available == -1)
        pipelines[pipeline_id].modules[module_idx].default_effort_param_id = -1;
        pipelines[pipeline_id].modules[module_idx].low_effort_param_id = -1;
        pipelines[pipeline_id].modules[module_idx].medium_effort_param_id = -1;
        pipelines[pipeline_id].modules[module_idx].high_effort_param_id = -1;

        // Set the param ids for available implementations
        for (size_t impl_idx = 0; impl_idx < mdef->n_implementations; impl_idx++)
        {
            Implementation *impl = mdef->implementations[impl_idx];
            switch (impl->effort_level)
            {
            case EFFORT_LEVEL__DEFAULT:
                pipelines[pipeline_id].modules[module_idx].default_effort_param_id = impl->param_id - 1; // Minus 1 to convert to zero-based index
                break;
            case EFFORT_LEVEL__LOW:
                pipelines[pipeline_id].modules[module_idx].low_effort_param_id = impl->param_id - 1;
                break;
            case EFFORT_LEVEL__MEDIUM:
                pipelines[pipeline_id].modules[module_idx].medium_effort_param_id = impl->param_id - 1;
                break;
            case EFFORT_LEVEL__HIGH:
                pipelines[pipeline_id].modules[module_idx].high_effort_param_id = impl->param_id - 1;
                break;
            default:
                // Handle unknown effort level
                break;
            }
        }
    }

    /* Free the unpacked pipeline definition data */
    pipeline_definition__free_unpacked(pdef, NULL);
}

void setup_module_config(param_t *param, int index)
{
    uint8_t *buffer = NULL;
    size_t buf_size = get_param_buffer(&buffer, param);
    uint32_t hash = murmur3_32(buffer, buf_size, 42);

    ModuleConfig *mcon = module_config__unpack(NULL, buf_size, buffer);
    free(buffer);
    buffer = NULL;

    if (!mcon)
    {
        return; // Skip this module if unpacking fails
    }

    // print the module config for debugging
    printf("Module Config: ID=%d, Num Parameters=%zu, Latency Cost=%d, Energy Cost=%d, Hash=%u\n",
           param->id - MODULE_PARAMID_OFFSET,
           mcon->n_parameters,
           mcon->latency_cost,
           mcon->energy_cost,
           hash);

    for (size_t i = 0; i < mcon->n_parameters; i++)
    {
        ConfigParameter *cparam = mcon->parameters[i];
        printf("  Parameter %zu: Key=%s, Value Case=%d\n", i, cparam->key, cparam->value_case);
        switch (cparam->value_case)
        {
        case CONFIG_PARAMETER__VALUE_BOOL_VALUE:
            printf("    Bool Value=%d\n", cparam->bool_value);
            break;
        case CONFIG_PARAMETER__VALUE_INT_VALUE:
            printf("    Int Value=%d\n", cparam->int_value);
            break;
        case CONFIG_PARAMETER__VALUE_FLOAT_VALUE:
            printf("    Float Value=%f\n", cparam->float_value);
            break;
        case CONFIG_PARAMETER__VALUE_STRING_VALUE:
            printf("    String Value=%s\n", cparam->string_value);
            break;
        default:
            break;
        }
    }

    int module_id = param->id - MODULE_PARAMID_OFFSET; // Minus 30 cause IDs are offset by 30 to accommodate pipeline ids (see pipeline.h)
    module_parameter_lists[module_id].n_parameters = mcon->n_parameters;
    module_parameter_lists[module_id].latency_cost = mcon->latency_cost;
    module_parameter_lists[module_id].energy_cost = mcon->energy_cost;
    module_parameter_lists[module_id].hash = hash;
    module_parameter_lists[module_id].parameters = malloc(mcon->n_parameters * sizeof(ModuleParameter *));
    if (!module_parameter_lists[module_id].parameters) // Check if malloc failed
    {
        set_error_param(MEMORY_MALLOC);
        module_config__free_unpacked(mcon, NULL);
        return;
    }

    for (size_t i = 0; i < mcon->n_parameters; i++)
    {
        module_parameter_lists[module_id].parameters[i] = malloc(sizeof(ModuleParameter));

        if (!module_parameter_lists[module_id].parameters[i]) // Check if malloc failed
        {
            set_error_param(MEMORY_MALLOC);
            // Cleanup previously allocated memory for parameters
            for (size_t j = 0; j < i; j++)
            {
                if (module_parameter_lists[module_id].parameters[j]->value_case == STRING_VALUE)
                {
                    free(module_parameter_lists[module_id].parameters[j]->string_value);
                }
                free(module_parameter_lists[module_id].parameters[j]);
            }
            free(module_parameter_lists[module_id].parameters);

            module_config__free_unpacked(mcon, NULL); // Assume there's a way to free mcon
            return;
        }

        module_parameter_lists[module_id].parameters[i]->key = strdup(mcon->parameters[i]->key);
        module_parameter_lists[module_id].parameters[i]->value_case = mcon->parameters[i]->value_case;

        switch (mcon->parameters[i]->value_case)
        {
        case CONFIG_PARAMETER__VALUE_BOOL_VALUE:
            module_parameter_lists[module_id].parameters[i]->bool_value = mcon->parameters[i]->bool_value;
            break;
        case CONFIG_PARAMETER__VALUE_INT_VALUE:
            module_parameter_lists[module_id].parameters[i]->int_value = mcon->parameters[i]->int_value;
            break;
        case CONFIG_PARAMETER__VALUE_FLOAT_VALUE:
            module_parameter_lists[module_id].parameters[i]->float_value = mcon->parameters[i]->float_value;
            break;
        case CONFIG_PARAMETER__VALUE_STRING_VALUE:
            module_parameter_lists[module_id].parameters[i]->string_value = strdup(mcon->parameters[i]->string_value);
            break;
        default:
            break;
        }
    }

    /* Free the unpacked module config data */
    module_config__free_unpacked(mcon, NULL);
}

void setup_all_pipelines()
{
    for (size_t pipeline_idx = 0; pipeline_idx < MAX_PIPELINES; pipeline_idx++)
    {
        setup_pipeline(pipeline_config_params[pipeline_idx], 0);
    }
}

void setup_all_module_configs()
{
    for (size_t module_idx = 0; module_idx < MAX_MODULES; module_idx++)
    {
        setup_module_config(module_config_params[module_idx], 0);
    }
}

void setup_cache_if_needed()
{
    if (!is_setup)
    {
        // Fetch and setup pipeline and module configurations if not done
        printf("rebuilding cache \r\n");
        MTR_BEGIN_FUNC();
        setup_all_pipelines();
        setup_all_module_configs();
        MTR_END_FUNC();
        is_setup = 1;
    }
}

void invalidate_cache()
{
    printf("invalidating cache \r\n");
    MTR_INSTANT_FUNC();
    is_setup = 0;
}
