#ifndef DIPP_PRIORITY_QUEUE_H
#define DIPP_PRIORITY_QUEUE_H

#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include "dipp_process.h"

// Define maximum size of the priority queue
#define MAX 100
#define MAX_PARTIAL_QUEUE_SIZE 10

// Define PriorityQueue structure
typedef struct PriorityQueue
{
    ImageBatch items[MAX];
    int size;
    pthread_mutex_t lock;
} PriorityQueue;

typedef struct PriorityQueueImpl
{
    int *(*init)(PriorityQueue *pq, char *filename);
    int (*enqueue)(PriorityQueue *pq, ImageBatch item);
    ImageBatch *(*dequeue)(PriorityQueue *pq);
    ImageBatch *(*peek)(PriorityQueue *pq);
    size_t (*get_queue_size)(PriorityQueue *pq);
} PriorityQueueImpl;

PriorityQueueImpl *get_priority_queue_impl(StorageMode storage_type);

extern PriorityQueueImpl *priority_queue_mmap;
extern PriorityQueueImpl *priority_queue_mem;

#endif // DIPP_PRIORITY_QUEUE_H