#include "priority_queue.h"
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <string.h>

int init_pq_mem(PriorityQueue *pq, char *filename)
{
    pthread_mutex_init(&pq->lock, NULL);
    pq->size = 0;
    return 0;
}

// Define enqueue function to add an item to the queue
int enqueue_mem(PriorityQueue *pq, ImageBatch item)
{
    pthread_mutex_lock(&pq->lock);

    if (pq->size == MAX)
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

PriorityQueueImpl priority_queue_mem = {
    .init = init_pq_mem,
    .enqueue = enqueue_mem,
    .dequeue = dequeue_mem,
    .peek = peek,
    .get_queue_size = get_queue_size};