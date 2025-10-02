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

Pipeline pipelines[MAX_PIPELINES];
ModuleParameterList module_parameter_lists[MAX_MODULES];

static int is_setup = 0;

int is_buffer_empty(uint8_t *buffer, size_t size) {
    for (size_t i = 0; i < size; i++) {
        if (buffer[i] != 0) {
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

    int pipeline_id = param->id - PIPELINE_PARAMID_OFFSET;
    pipelines[pipeline_id].pipeline_id = pipeline_id + 1;
    pipelines[pipeline_id].num_modules = pdef->n_modules;

    /* Free the unpacked pipeline definition data */
    pipeline_definition__free_unpacked(pdef, NULL);
}

void setup_module_config(param_t *param, int index)
{
    printf("empty");
}

void setup_all_pipelines()
{
    for (size_t pipeline_idx = 0; pipeline_idx < MAX_PIPELINES; pipeline_idx++)
    {
        setup_pipeline(pipeline_config_params[pipeline_idx], 0);
    }
}

void setup_cache_if_needed()
{
    if (!is_setup)
    {
        // Fetch and setup pipeline and module configurations if not done
	    printf("rebuilding cache \r\n");
        setup_all_pipelines();
        is_setup = 1;
    }
}

void invalidate_cache()
{
    printf("invalidating cache \r\n");
    is_setup = 0;
}
