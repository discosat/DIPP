#ifndef DIPP_DTP_SERVER_H
#define DIPP_DTP_SERVER_H

#include "metadata.pb-c.h"

typedef struct RingBufferElementMetadata {
    uint32_t index;
    uint32_t metadata_len;
    Metadata metadata;
} RingBufferElementMetadata;

typedef struct RingBufferMetadata {
    uint32_t size;
    uint32_t elements_len;
    RingBufferElementMetadata *elements;
} RingBufferMetadata;

#endif