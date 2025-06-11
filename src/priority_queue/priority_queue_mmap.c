#include "priority_queue.h"
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <string.h>

int init_pq_mmap(PriorityQueue *pq, char *filename)
{

    // Use mmap to create a file-backed memory mapping
    int fd = open(filename, O_RDONLY);
    if (fd != -1)
    {
        // file exists, memory map it
        pq = mmap(NULL, sizeof(PriorityQueue), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (pq == MAP_FAILED)
        {
            printf("Failed to memory map queue file\n");
            close(fd);
            return -1;
        }
    }
    else
    {
        // file doesn't exist, create a new queue
        fd = open(filename, O_RDWR | O_CREAT, 0666);
        if (fd == -1)
        {
            printf("Failed to create queue file\n");
            close(fd);
            return -1;
        }

        // Set the file size to match the size of the PriorityQueue structure
        if (ftruncate(fd, sizeof(PriorityQueue)) == -1)
        {
            printf("Failed to set file size\n");
            close(fd);
            return -1;
        }

        // Memory map the file
        pq = mmap(NULL, sizeof(PriorityQueue), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (pq == MAP_FAILED)
        {
            printf("Failed to memory map queue file\n");
            close(fd);
            return -1;
        }

        // Initialize the queue
        pq->size = 0;
        memset(pq->items, 0, sizeof(pq->items));
    }
    pthread_mutex_init(&pq->lock, NULL);
    close(fd);
    return 0;
}

// Define enqueue function to add an item to the queue
int enqueue_mmap(PriorityQueue *pq, ImageBatch item)
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

PriorityQueueImpl priority_queue_mmap = {
    .init = init_pq_mmap,
    .enqueue = enqueue_mmap,
    .dequeue = dequeue_mmap,
    .peek = peek,
    .get_queue_size = get_queue_size};