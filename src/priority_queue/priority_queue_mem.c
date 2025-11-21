#include "priority_queue.h"
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <string.h>
#include "utils/minitrace.h"

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
    MTR_BEGIN_FUNC();
    pthread_mutex_lock(&pq->lock);

    if (pq->size == MAX_QUEUE_SIZE)
    {
        pthread_mutex_unlock(&pq->lock);
        printf("Priority queue is full\n");
        MTR_END_FUNC();
        return -1; // full
    }

    // each process will later memory-map the contents into this pointer
    item.data = NULL;

    // printf("New item arrived in pq: \r\n");
    // printf("Number of images: %i\r\n", item.num_images);
    // printf("Batch size: %i\r\n", item.batch_size);
    // printf("Pipeline ID: %i\r\n", item.pipeline_id);
    // printf("Priority: %i\r\n", item.priority);
    // printf("Filename: %s\r\n", item.filename);
    // printf("UUID: %s\r\n", item.uuid);
    // printf("Progress: %i\r\n", item.progress);
    // printf("Storage mode: %i\r\n", item.storage_mode);

    pq->items[pq->size++] = item;
    heapifyUp(pq, pq->size - 1);

    // printf("Item enqueued. Here is the queue.\r\n");
    // for (int i = 0; i < pq->size; i++)
    // {
    //     // print data inside each item
    //     printf("Item %i:\r\n", i);
    //     printf("Number of images: %i\r\n", pq->items[i].num_images);
    //     printf("Batch size: %i\r\n", pq->items[i].batch_size);
    //     printf("Pipeline ID: %i\r\n", pq->items[i].pipeline_id);
    //     printf("Priority: %i\r\n", pq->items[i].priority);
    //     printf("Filename: %s\r\n", pq->items[i].filename);
    //     printf("UUID: %s\r\n", pq->items[i].uuid);
    //     printf("Progress: %i\r\n", pq->items[i].progress);
    //     printf("Storage mode: %i\r\n", pq->items[i].storage_mode);
    //     printf("----\r\n");
    // }

    pthread_mutex_unlock(&pq->lock);
    MTR_END_FUNC();
    return 0; // success
}

// Define dequeue function to remove an item from the queue
ImageBatch *dequeue_mem(PriorityQueue *pq)
{
    MTR_BEGIN_FUNC();
    pthread_mutex_lock(&pq->lock);

    if (!pq->size)
    {
        pthread_mutex_unlock(&pq->lock);
        // printf("Priority queue is empty\n");
        MTR_END_FUNC();
        return NULL;
    }

    // allocate a stable copy for the caller
    ImageBatch *res = malloc(sizeof(ImageBatch));
    if (!res)
    {
        pthread_mutex_unlock(&pq->lock);
        MTR_END_FUNC();
        return NULL;
    }

    *res = pq->items[0];                  // shallow copy of the item
    pq->items[0] = pq->items[--pq->size]; // move last into root
    heapifyDown(pq, 0);

    pthread_mutex_unlock(&pq->lock);

    MTR_END_FUNC();
    return res;
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