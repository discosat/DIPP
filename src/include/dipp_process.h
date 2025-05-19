#ifndef DIPP_PROCESS_H
#define DIPP_PROCESS_H

#include "dipp_config.h"

#define MSG_QUEUE_KEY 71

// Return codes
#define SUCCESS 0
#define FAILURE -1

PriorityQueue *ingest_pq;
PriorityQueue *partially_processed_pq;

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

typedef enum IMPLEMENTATION_PREFERENCE
{
    LATENCY = 0,
    ENERGY = 1,
    BEST_EFFORT = 2,
    // TODO: maybe aggregate of latency and energy
}

typedef struct ImageBatch
{
    long mtype;          /* message type to read from the message queue */
    int num_images;      /* amount of images */
    int batch_size;      /* size of the image batch */
    int pipeline_id;     /* id of pipeline to utilize for processing */
    int priority;        /* priority of the image batch, e.g. max_latency from SLOs */
    unsigned char *data; /* address to image data (in shared memory) */
    char filename[111];  /* filename of the image data */
    char uuid[37];       /* uuid of the image data */
    int progress;        /* index of the last processed module (-1 if not started) */
} ImageBatch;

typedef struct ImageBatchFingerprint
{
    int num_images;
    int batch_size;
    int pipeline_id;
} ImageBatchFingerprint;

typedef ImageBatch (*ProcessFunction)(ImageBatch *, ModuleParameterList *, int *);

#endif