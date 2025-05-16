#ifndef DIPP_PRIORITY_QUEUE_H
#define DIPP_RPIORITY_QUEUE_H
#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include "dipp_process.h"

// Define maximum size of the priority queue
#define MAX 100
#define MAX_PARTIAL_QUEUE_SIZE 10

// Define PriorityQueue structure
typedef struct
{
    ImageBatch items[MAX];
    int size;
    pthread_mutex_t lock;
} PriorityQueue;

PriorityQueue *createPriorityQueue(char *filename);
int enqueue(PriorityQueue *pq, ImageBatch item);
ImageBatch *dequeue(PriorityQueue *pq);
ImageBatch *peek(PriorityQueue *pq);
size_t get_queue_size(PriorityQueue *pq);

#endif // DIPP_PRIORITY_QUEUE_H