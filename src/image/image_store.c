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
#include <errno.h>
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

            /* Flush to storage BEFORE claiming the data is persisted. munmap does
               not imply durability -- it only drops the mapping, leaving the
               pages dirty in the page cache under a 30 s writeback expiry. The
               shared-memory segment has already been IPC_RMID'd above, so until
               this msync returns the batch exists in exactly one place: volatile
               page cache. A reset here lost the image outright. */
            if (msync(batch->data, batch->batch_size, MS_SYNC) == -1)
            {
                set_error_param(MMAP_UNMAP);
                return FAILURE;
            }

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
            // Open the existing persisted file. A missing or short file is an
            // error, never something to paper over by creating/extending it:
            // both would yield zeros that read as valid image data downstream.
            int fd;
            if (access(batch->filename, F_OK) == -1)
            {
                /* The batch names a file that is not there. Creating it and
                   ftruncate()ing to batch_size yields a mapping full of zeros,
                   which used to be handed onward as if it were image data --
                   the pipeline then "succeeded" and uploaded nothing. Fail
                   loudly instead: a missing file is a durability bug, and
                   silently substituting zeros is what hid it. */
                printf("ERROR: batch %s references missing file %s (%d bytes expected); "
                       "refusing to fabricate zero-filled data\n",
                       batch->uuid, batch->filename, batch->batch_size);
                set_error_param(MMAP_NOT_FOUND);
                return FAILURE;
            }
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
                /* Short file: the previous stage did not finish writing it.
                   Extending it would pad with zeros and hide the truncation,
                   so treat it the same as a missing file. */
                printf("ERROR: batch %s file %s is %lld bytes, expected %d; "
                       "refusing to pad with zeros\n",
                       batch->uuid, batch->filename,
                       (long long)st.st_size, batch->batch_size);
                close(fd);
                set_error_param(MMAP_NOT_FOUND);
                return FAILURE;
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
/* Release a batch's storage for good: the shared-memory segment (STORAGE_MEM)
 * or the data file (STORAGE_MMAP). Called once a batch has been uploaded or
 * quarantined -- never for a batch that is still queued.
 *
 * image_batch_cleanup only detaches/unmaps, which is right between modules,
 * but nothing ever removed the FINAL segment of a completed batch: the last
 * module reuses the input segment when its output fits, so the segment left
 * behind is the largest one the pipeline touched (up to 30 MB of demosaiced
 * frame). Measured on the board: 61 leaked segments holding 1.59 GB of the
 * 1.95 GB after one session, until the first module to claim a frame died.
 * For MMAP the file stayed too, reclaimed only by the startup orphan scan
 * (412 files, 2.6 GB after one run). */
int image_batch_release(ImageBatch *batch)
{
    if (!batch)
        return FAILURE;
    int result = image_batch_cleanup(batch);
    switch (batch->storage_mode)
    {
    case STORAGE_MEM:
        if (batch->shmid >= 0)
        {
            if (shmctl(batch->shmid, IPC_RMID, NULL) == -1)
            {
                set_error_param(SHM_REMOVE);
                result = FAILURE;
            }
            batch->shmid = -1;
        }
        break;
    case STORAGE_MMAP:
        if (batch->filename[0] != '\0' && strncmp(batch->filename, "/usr/share/dipp/data/", 21) == 0)
        {
            if (remove(batch->filename) == -1 && errno != ENOENT)
            {
                set_error_param(MMAP_REMOVE);
                result = FAILURE;
            }
        }
        break;
    default:
        break;
    }
    return result;
}
