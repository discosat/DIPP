#include "priority_queue.h"
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

    // sync to disk
    msync(pq, sizeof(PriorityQueue), MS_SYNC);

    pthread_mutex_unlock(&pq->lock);
    return 0; // success
}

// Define dequeue function to remove an item from the queue
ImageBatch *dequeue_mmap(PriorityQueue *pq)
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