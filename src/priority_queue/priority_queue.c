#include "dipp_process.h"
#include "priority_queue.h"

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