#include "priority_queue.h"
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <string.h>

int createPriorityQueue(PriorityQueue *pq, char *filename)
{
    pthread_mutex_init(&pq->lock, NULL);
    pq->size = 0;
    return 0;
}

// Define swap function to swap two integers
void swap(ImageBatch *a, ImageBatch *b)
{
    ImageBatch temp = *a;
    *a = *b;
    *b = temp;
}

// Define heapifyUp function to maintain heap property
// during insertion
void heapifyUp(PriorityQueue *pq, int index)
{
    if (index && pq->items[(index - 1) / 2].priority > pq->items[index].priority)
    {
        swap(&pq->items[(index - 1) / 2],
             &pq->items[index]);
        heapifyUp(pq, (index - 1) / 2);
    }
}

// Define enqueue function to add an item to the queue
int enqueue(PriorityQueue *pq, ImageBatch item)
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

    // sync to disk
    msync(pq, sizeof(PriorityQueue), MS_SYNC);

    pthread_mutex_unlock(&pq->lock);
    return 0; // success
}

// Define heapifyDown function to maintain heap property
// during deletion
int heapifyDown(PriorityQueue *pq, int index)
{
    int smallest = index;
    int left = 2 * index + 1;
    int right = 2 * index + 2;

    if (left < pq->size && pq->items[left].priority < pq->items[smallest].priority)
        smallest = left;

    if (right < pq->size && pq->items[right].priority < pq->items[smallest].priority)
        smallest = right;

    if (smallest != index)
    {
        swap(&pq->items[index], &pq->items[smallest]);
        heapifyDown(pq, smallest);
    }
}

// Define dequeue function to remove an item from the queue
ImageBatch *dequeue(PriorityQueue *pq)
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

    // sync to disk
    msync(pq, sizeof(PriorityQueue), MS_SYNC);

    pthread_mutex_unlock(&pq->lock);

    return item;
}

// Define peek function to get the top item from the queue
ImageBatch *peek(PriorityQueue *pq)
{
    pthread_mutex_lock(&pq->lock);
    if (!pq->size)
    {
        pthread_mutex_unlock(&pq->lock);
        printf("Priority queue is empty\n");
        return NULL;
    }

    ImageBatch *item = &pq->items[0];

    pthread_mutex_unlock(&pq->lock);

    return item;
}

size_t get_queue_size(PriorityQueue *pq)
{
    pthread_mutex_lock(&pq->lock);
    size_t size = pq->size;
    pthread_mutex_unlock(&pq->lock);
    return size;
}

PriorityQueueImpl priority_queue_mem = {
    .init = createPriorityQueue,
    .enqueue = enqueue,
    .dequeue = dequeue,
    .peek = peek,
    .get_queue_size = get_queue_size};