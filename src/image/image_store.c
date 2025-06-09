#include "image_store.h"
#include "dipp_error.h"
#include "dipp_process.h"
#include "vmem_upload_local.h"
#include <sys/mman.h>
#include <sys/shm.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

int image_batch_read_data(ImageBatch *batch)
{
    if (!batch)
    {
        return FAILURE;
    }

    switch (batch->storage_mode)
    {
    case STORAGE_MMAP:
    {
        // Memory-mapped file access
        int fd = open(batch->filename, O_RDONLY, 0644);
        if (fd == -1)
        {
            set_error_param(MMAP_OPEN);
            return FAILURE;
        }

        batch->data = mmap(NULL, batch->batch_size, PROT_READ, MAP_PRIVATE, fd, 0);
        close(fd);

        if (batch->data == MAP_FAILED)
        {
            set_error_param(MMAP_ATTACH);
            return FAILURE;
        }

        break;
    }
    case STORAGE_MEM:
    {
        // Shared memory access
        batch->data = shmat(batch->shmid, NULL, 0);
        if (batch->data == (void *)-1)
        {
            set_error_param(SHM_ATTACH);
            return FAILURE;
        }

        break;
    }
    case STORAGE_NOT_SET:
    default:
        return FAILURE;
    }

    return SUCCESS;
}

int image_batch_cleanup(ImageBatch *batch)
{
    if (!batch)
    {
        return FAILURE;
    }

    int result = SUCCESS;

    switch (batch->storage_mode)
    {
    case STORAGE_MMAP:
    {
        // Unmap memory-mapped file
        if (batch->data && munmap(batch->data, batch->batch_size) == -1)
        {
            set_error_param(MMAP_UNMAP);
            result = FAILURE;
        }
        break;
    }
    case STORAGE_MEM:
    {
        // Detach from shared memory
        if (batch->data && shmdt(batch->data) == -1)
        {
            set_error_param(SHM_DETACH);
            result = FAILURE;
        }
        break;
    }
    case STORAGE_NOT_SET:
    default:
        break;
    }

    // Clear the data pointer
    batch->data = NULL;

    return result;
}

int image_batch_setup_storage(ImageBatch *batch, StorageMode storage_mode)
{
    if (!batch)
    {
        return FAILURE;
    }

    batch->storage_mode = storage_mode;
    batch->data = NULL; // Will be set in read_data

    return SUCCESS;
}

// TODO: implement switch to the persisted variant. Images will most likely come from camera
// controller through shared memory, but dipp might start in persisted mode
// (check its filename[0]==\0 && storage_mode == STORAGE_MMAP, if so, persist)