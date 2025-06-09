#ifndef DIPP_PROCESS_H
#define DIPP_PROCESS_H

#include "dipp_config.h"
#include "priority_queue.h"
#include "cost_store.h"

#define MSG_QUEUE_KEY 71

// Return codes
#define SUCCESS 0
#define FAILURE -1

#define DEFAULT_EFFORT_LATENCY 3000
#define DEFAULT_EFFORT_ENERGY 3000
#define LOW_EFFORT_LATENCY 2000
#define LOW_EFFORT_ENERGY 2000
#define MEDIUM_EFFORT_LATENCY 3000
#define MEDIUM_EFFORT_ENERGY 3000
#define HIGH_EFFORT_LATENCY 4000
#define HIGH_EFFORT_ENERGY 4000

extern PriorityQueue *ingest_pq;
extern PriorityQueue *partially_processed_pq;
extern CostEntry *cost_cache;
StorageMode global_storage_mode;

// Pipeline run codes
typedef enum PIPELINE_PROCESS
{
    PROCESS_STOP = 0,
    PROCESS_ONE = 1,
    PROCESS_ALL = 2,
    PROCESS_WAIT_ONE = 3,
    PROCESS_WAIT_ALL = 4
} PIPELINE_PROCESS;

typedef enum COST_MODEL_LOOKUP_RESULT
{
    FOUND_CACHED = 0,     // got the optimal implementation from cost model
    NOT_FOUND = -1,       // could not find implementation that meets the requirements
    FOUND_NOT_CACHED = -2 // found an implementation that fulfils requirements, but not cached
} COST_MODEL_LOOKUP_RESULT;

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

typedef enum
{
    STORAGE_MMAP,
    STORAGE_MEM,
    STORAGE_NOT_SET
} StorageMode;

#endif