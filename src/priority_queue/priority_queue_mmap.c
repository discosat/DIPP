#include "priority_queue.h"
#include "utils/minitrace.h"
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <string.h>
#include <sys/stat.h>
#include <errno.h>
#include <stdio.h>

// mmap init: map file and set *pq to mapped region. If file is new, zero it and msync.
int init_pq_mmap(PriorityQueue **pq, char *filename)
{
    const char *file = filename;
    if (pq == NULL || file == NULL)
    {
        printf("Error: provided PriorityQueue** or filename is NULL\n");
        return -1;
    }

    struct stat st;
    int exists = (stat(file, &st) == 0);

    int fd = open(file, O_RDWR | O_CREAT, 0666);
    if (fd == -1)
    {
        printf("Failed to open/create queue file: %s (%s)\n", file, strerror(errno));
        return -1;
    }

    size_t size = sizeof(PriorityQueue);
    if (ftruncate(fd, size) == -1)
    {
        printf("Failed to set file size: %s (%s)\n", file, strerror(errno));
        close(fd);
        return -1;
    }

    void *mapped = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (mapped == MAP_FAILED)
    {
        printf("Failed to memory map queue file: %s (%s)\n", file, strerror(errno));
        close(fd);
        return -1;
    }

    if (!exists)
    {
        memset(mapped, 0, size);
        msync(mapped, size, MS_SYNC);
    }

    // return mapped region to caller
    *pq = (PriorityQueue *)mapped;

    // initialize mutex and size (if newly created)
    if (!exists)
    {
        (*pq)->size = 0;
    }
    pthread_mutex_init(&(*pq)->lock, NULL);

    // keep mapping alive; close fd
    close(fd);
    return 0;
}

// Define enqueue function to add an item to the queue
int enqueue_mmap(PriorityQueue *pq, ImageBatch item)
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

    // sync to disk
    msync(pq, sizeof(PriorityQueue), MS_SYNC);

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
ImageBatch *dequeue_mmap(PriorityQueue *pq)
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

    // allocate a stable copy for the caller (so returned pointer isn't into the mmap region)
    ImageBatch *res = malloc(sizeof(ImageBatch));
    if (!res)
    {
        pthread_mutex_unlock(&pq->lock);
        MTR_END_FUNC();
        return NULL;
    }

    *res = pq->items[0]; // shallow copy of the item
    pq->items[0] = pq->items[--pq->size];
    heapifyDown(pq, 0);

    // sync to disk
    msync(pq, sizeof(PriorityQueue), MS_SYNC);

    pthread_mutex_unlock(&pq->lock);

    MTR_END_FUNC();
    return res;
}

int clean_up_pq_mmap(PriorityQueue *pq)
{
    pthread_mutex_destroy(&pq->lock);
    if (pq)
    {
        munmap(pq, sizeof(PriorityQueue));
    }
    return 0;
}

PriorityQueueImpl priority_queue_mmap = {
    .init = init_pq_mmap,
    .enqueue = enqueue_mmap,
    .dequeue = dequeue_mmap,
    .peek = peek,
    .get_queue_size = get_queue_size,
    .clean_up = clean_up_pq_mmap};