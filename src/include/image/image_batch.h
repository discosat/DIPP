#ifndef DIPP_IMAGE_BATCH_H
#define DIPP_IMAGE_BATCH_H

#include <stdint.h>
#include "dipp_config.h"

typedef enum StorageMode
{
    STORAGE_MMAP,    /* every module output persisted (per-module checkpoint) */
    STORAGE_MEM,     /* nothing persisted; shared memory throughout */
    STORAGE_NOT_SET,
    STORAGE_HYBRID   /* durable at entry, exit and scheduler halts; shared
                        memory between modules. Modules never see this value:
                        the daemon presents a HYBRID batch to them as
                        STORAGE_MEM, so the modules' copy of this enum (which
                        stops at STORAGE_NOT_SET) is unaffected. */
} StorageMode;

typedef struct ImageBatch
{
    long mtype;               /* message type to read from the message queue */
    int num_images;           /* amount of images */
    int batch_size;           /* size of the image batch */
    int pipeline_id;          /* id of pipeline to utilize for processing */
    int64_t priority;         /* absolute deadline, CLOCK_MONOTONIC MICROSECONDS.
                                 Signed so a missed deadline stays negative and measurable. */
    int64_t capture_us;       /* capture time, CLOCK_MONOTONIC MICROSECONDS (set by the
                                 producer). The scheduler reads arrival cadence and each
                                 frame's own deadline window (priority - capture_us) from
                                 it. MUST match the other copy of this struct (DIPP
                                 src/include/image/image_batch.h, tf-dipp-module
                                 src/include/types.h): it crosses the message queue raw. */
    unsigned char *data;      /* address to image data (in shared memory) */
    char filename[111];       /* filename of the image data */
    int shmid;                /* shared memory id for the image data */
    char uuid[37];            /* uuid of the image data */
    int progress;             /* index of the last processed module (-1 if not started) */
    StorageMode storage_mode; /* storage mode for the image data */
} ImageBatch;

typedef struct ImageBatchFingerprint
{
    int num_images;
    int batch_size;
    int pipeline_id;
} ImageBatchFingerprint;

typedef ImageBatch (*ProcessFunction)(ImageBatch *, ModuleParameterList *, int *);

#endif // DIPP_IMAGE_BATCH_H