#include "image_batch.h"
#include "priority_queue.h"
#include "utils/minitrace.h"

PriorityQueueImpl *get_priority_queue_impl(StorageMode storage_type)
{
    switch (storage_type)
    {
    case STORAGE_MMAP:
        return &priority_queue_mmap;
    case STORAGE_MEM:
        return &priority_queue_mem;
    default:
        fprintf(stderr, "Unsupported storage mode\n");
        return NULL;
    }
}

// Define swap function to swap two integers
void swap(ImageBatch *a, ImageBatch *b)
{
    ImageBatch temp = *a;
    *a = *b;
    *b = temp;
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

// Define heapifyDown function to maintain heap property
// during deletion
void heapifyDown(PriorityQueue *pq, int index)
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

size_t get_queue_size(PriorityQueue *pq)
{
    MTR_BEGIN_FUNC();
    pthread_mutex_lock(&pq->lock);
    size_t size = pq->size;
    pthread_mutex_unlock(&pq->lock);
    MTR_END_FUNC();
    return size;
}