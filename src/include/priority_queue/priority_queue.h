#ifndef DIPP_PRIORITY_QUEUE_H
#define DIPP_PRIORITY_QUEUE_H

#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include "image_batch.h"

// Define maximum size of the priority queue
#define MAX_QUEUE_SIZE 100
#define MAX_PARTIAL_QUEUE_SIZE 10
#define LOW_QUEUE_DEPTH_THRESHOLD 5
#define PARTIAL_QUEUE_SIZE_THRESHOLD 5

// Define PriorityQueue structure
typedef struct PriorityQueue
{
    ImageBatch items[MAX_QUEUE_SIZE];
    int size;
    pthread_mutex_t lock;
} PriorityQueue;

typedef struct PriorityQueueImpl
{
    // init takes PriorityQueue ** so the implementation can allocate or mmap the outer struct and assign *pq
    int (*init)(PriorityQueue **pq, char *filename);
    int (*enqueue)(PriorityQueue *pq, ImageBatch item);
    ImageBatch *(*dequeue)(PriorityQueue *pq);
    ImageBatch *(*peek)(PriorityQueue *pq);
    /* Copy the head out under the lock without removing it. Returns SUCCESS/0
       if an item was copied, -1 if the queue is empty. This is the read half of
       the peek-process-commit protocol: the batch stays durably queued for the
       whole time it is being processed, so a reset mid-pipeline leaves it there
       to be re-run rather than losing it. */
    int (*peek_copy)(PriorityQueue *pq, ImageBatch *out);
    /* Overwrite the head in place and msync. Used to record progress on a
       partially processed batch without removing and re-adding it, which would
       otherwise put two entries for the same batch in the queue. Ordering is by
       `priority`, which this never changes, so no re-heapify is needed. */
    int (*commit_head)(PriorityQueue *pq, const ImageBatch *item);
    size_t (*get_queue_size)(PriorityQueue *pq);
    int (*clean_up)(PriorityQueue *pq);
} PriorityQueueImpl;

PriorityQueueImpl *get_priority_queue_impl(StorageMode storage_type);
ImageBatch *peek(PriorityQueue *pq);
int peek_copy_generic(PriorityQueue *pq, ImageBatch *out);
void heapifyDown(PriorityQueue *pq, int index);
void heapifyUp(PriorityQueue *pq, int index);
size_t get_queue_size(PriorityQueue *pq);

extern PriorityQueueImpl priority_queue_mmap;
extern PriorityQueueImpl priority_queue_mem;

extern PriorityQueueImpl *pq_impl;

#endif // DIPP_PRIORITY_QUEUE_H