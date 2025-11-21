#include "image_store.h"
#include "dipp_error.h"
#include "dipp_process.h"
#include "vmem_upload_local.h"
#include <sys/mman.h>
#include <sys/shm.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <uuid/uuid.h>

int persist_data_if_necessary(ImageBatch *batch)
{
    if (!batch)
    {
        return FAILURE;
    }
    switch (batch->storage_mode)
    {
    case STORAGE_MMAP:
    {
        // Check if the filename is set
        if (batch->filename[0] == '\0')
        {
            // this means that DIPP is running in persisted mode but data came from shared memory
            char file_uuid[37];
            uuid_t uuid;
            uuid_generate_random(uuid);
            uuid_unparse_lower(uuid, file_uuid);

            char filename_prefix[] = "/usr/share/dipp/data/batch_%s_%s.bin";
            char batch_filename[sizeof(filename_prefix) + 37 + 37];
            snprintf(batch_filename, sizeof(filename_prefix) + 37 + 37, filename_prefix, batch->uuid, file_uuid);

            strncpy(batch->filename, batch_filename, sizeof(batch->filename) - 1);
            batch->filename[sizeof(batch->filename) - 1] = '\0'; // Ensure null termination

            // Open new file for memory-mapped storage
            int fd = open(batch->filename, O_RDWR | O_CREAT | O_TRUNC, 0644);
            if (fd == -1)
            {
                set_error_param(MMAP_OPEN);
                return FAILURE;
            }

            // Ensure file is large enough for the mapping
            if (ftruncate(fd, batch->batch_size) == -1)
            {
                close(fd);
                set_error_param(MMAP_OPEN);
                return FAILURE;
            }

            batch->data = mmap(NULL, batch->batch_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
            close(fd);

            if (batch->data == MAP_FAILED)
            {
                set_error_param(MMAP_MAP);
                return FAILURE;
            }

            // Shared memory access
            unsigned char *shm_data = shmat(batch->shmid, NULL, 0);
            if (shm_data == (void *)-1)
            {
                set_error_param(SHM_ATTACH);
                return FAILURE;
            }

            memcpy(batch->data, shm_data, batch->batch_size);

            // Detach from shared memory
            if (shmdt(shm_data) == -1)
            {
                set_error_param(SHM_DETACH);
                return FAILURE;
            }

            // Remove the shared memory segment
            if (shmctl(batch->shmid, IPC_RMID, NULL) == -1)
            {
                set_error_param(SHM_REMOVE);
                return FAILURE;
            }
            // Clear the shared memory ID
            batch->shmid = -1;

            // detach the mmaped data as it is now persisted
            if (munmap(batch->data, batch->batch_size) == -1)
            {
                set_error_param(MMAP_UNMAP);
                return FAILURE;
            }
            batch->data = NULL;
        }
        break;
    }
    default:
        // no need to do anything
        break;
    }
    return SUCCESS;
}

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

        // Check if the filename is set
        if (batch->filename[0] == '\0')
        {
            // this means that DIPP is running in persisted mode but data came from shared memory
            char file_uuid[37];
            uuid_t uuid;
            uuid_generate_random(uuid);
            uuid_unparse_lower(uuid, file_uuid);

            char filename_prefix[] = "/usr/share/dipp/data/batch_%s_%s.bin";
            char batch_filename[sizeof(filename_prefix) + 37 + 37];
            snprintf(batch_filename, sizeof(filename_prefix) + 37 + 37, filename_prefix, batch->uuid, file_uuid);

            strncpy(batch->filename, batch_filename, sizeof(batch->filename) - 1);
            batch->filename[sizeof(batch->filename) - 1] = '\0'; // Ensure null termination

            // Memory-mapped file access
            // Open writable and ensure file is large enough before mmap
            int fd = open(batch->filename, O_RDWR | O_CREAT | O_TRUNC, 0644);
            if (fd == -1)
            {
                set_error_param(MMAP_OPEN);
                return FAILURE;
            }

            if (ftruncate(fd, batch->batch_size) == -1)
            {
                close(fd);
                set_error_param(MMAP_OPEN);
                return FAILURE;
            }

            batch->data = mmap(NULL, batch->batch_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
            close(fd);

            if (batch->data == MAP_FAILED)
            {
                set_error_param(MMAP_MAP);
                return FAILURE;
            }

            // Shared memory access
            unsigned char *shm_data = shmat(batch->shmid, NULL, 0);
            if (shm_data == (void *)-1)
            {
                set_error_param(SHM_ATTACH);
                return FAILURE;
            }

            // Copy data from shared memory to memory-mapped file
            memcpy(batch->data, shm_data, batch->batch_size);

            // Detach from shared memory
            if (shmdt(shm_data) == -1)
            {
                set_error_param(SHM_DETACH);
                return FAILURE;
            }

            // Remove the shared memory segment
            if (shmctl(batch->shmid, IPC_RMID, NULL) == -1)
            {
                set_error_param(SHM_REMOVE);
                return FAILURE;
            }
            // Clear the shared memory ID
            batch->shmid = -1;
        }
        else
        {
            // Memory-mapped file access
            // Open existing file without truncating it (don't erase persisted data).
            // Create it only if it doesn't exist. If the file is smaller than the batch
            // size, extend it.
            int fd;
            if (access(batch->filename, F_OK) == -1)
            {
                fd = open(batch->filename, O_RDWR | O_CREAT, 0644);
                if (fd == -1)
                {
                    set_error_param(MMAP_OPEN);
                    return FAILURE;
                }
                if (ftruncate(fd, batch->batch_size) == -1)
                {
                    close(fd);
                    set_error_param(MMAP_OPEN);
                    return FAILURE;
                }
            }
            else
            {
                fd = open(batch->filename, O_RDWR);
                if (fd == -1)
                {
                    set_error_param(MMAP_OPEN);
                    return FAILURE;
                }
                struct stat st;
                if (fstat(fd, &st) == -1)
                {
                    close(fd);
                    set_error_param(MMAP_OPEN);
                    return FAILURE;
                }
                if ((size_t)st.st_size < batch->batch_size)
                {
                    if (ftruncate(fd, batch->batch_size) == -1)
                    {
                        close(fd);
                        set_error_param(MMAP_OPEN);
                        return FAILURE;
                    }
                }
            }

            batch->data = mmap(NULL, batch->batch_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
            close(fd);

            if (batch->data == MAP_FAILED)
            {
                set_error_param(MMAP_MAP);
                return FAILURE;
            }
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
    // printf("Setting up storage mode %d for image batch\r\n", storage_mode);
    if (!batch)
    {
        return FAILURE;
    }

    batch->storage_mode = storage_mode;
    batch->data = NULL; // Will be set in read_data

    return persist_data_if_necessary(batch);
}