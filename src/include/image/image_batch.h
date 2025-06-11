#ifndef DIPP_IMAGE_BATCH_H
#define DIPP_IMAGE_BATCH_H

#include "dipp_config.h"

typedef enum StorageMode
{
    STORAGE_MMAP,
    STORAGE_MEM,
    STORAGE_NOT_SET
} StorageMode;

typedef struct ImageBatch
{
    long mtype;               /* message type to read from the message queue */
    int num_images;           /* amount of images */
    int batch_size;           /* size of the image batch */
    int pipeline_id;          /* id of pipeline to utilize for processing */
    int priority;             /* priority of the image batch, e.g. max_latency from SLOs */
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