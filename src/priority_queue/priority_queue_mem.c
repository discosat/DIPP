#include "priority_queue.h"
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <string.h>

// Initialize in-memory priority queue: allocate outer PriorityQueue if *pq is NULL and init it
int init_pq_mem(PriorityQueue **pq, char *filename)
{
    (void)filename;
    if (pq == NULL)
    {
        printf("Error: provided PriorityQueue** is NULL\n");
        return -1;
    }

    if (*pq == NULL)
    {
        *pq = malloc(sizeof(PriorityQueue));
        if (!*pq)
        {
            printf("Failed to allocate memory for priority queue\n");
            return -1;
        }
    }

    // initialize fields
    memset((*pq)->items, 0, sizeof((*pq)->items));
    (*pq)->size = 0;
    pthread_mutex_init(&(*pq)->lock, NULL);

    return 0;
}

// Define enqueue function to add an item to the queue
int enqueue_mem(PriorityQueue *pq, ImageBatch item)
{
    pthread_mutex_lock(&pq->lock);

    if (pq->size == MAX_QUEUE_SIZE)
    {
        pthread_mutex_unlock(&pq->lock);
        printf("Priority queue is full\n");
        return -1; // full
    }

    // each process will later memory-map the contents into this pointer
    item.data = NULL;

    pq->items[pq->size++] = item;
    heapifyUp(pq, pq->size - 1);

    pthread_mutex_unlock(&pq->lock);
    return 0; // success
}

// Define dequeue function to remove an item from the queue
ImageBatch *dequeue_mem(PriorityQueue *pq)
{
    pthread_mutex_lock(&pq->lock);

    if (!pq->size)
    {
        pthread_mutex_unlock(&pq->lock);
        printf("Priority queue is empty\n");
        return NULL;
    }

    ImageBatch *item = &pq->items[0];
    pq->items[0] = pq->items[--pq->size];
    heapifyDown(pq, 0);

    pthread_mutex_unlock(&pq->lock);

    return item;
}

int clean_up_pq_mem(PriorityQueue *pq)
{
    pthread_mutex_destroy(&pq->lock);
    if (pq)
    {
        free(pq);
    }
    return 0;
}

PriorityQueueImpl priority_queue_mem = {
    .init = init_pq_mem,
    .enqueue = enqueue_mem,
    .dequeue = dequeue_mem,
    .peek = peek,
    .get_queue_size = get_queue_size,
    .clean_up = clean_up_pq_mem};