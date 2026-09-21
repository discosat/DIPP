#ifndef DIPP_IMAGE_STORE_H
#define DIPP_IMAGE_STORE_H

#include "image_batch.h"

/**
 * Read image batch data based on storage mode
 * @param batch Pointer to ImageBatch structure
 * @return status code
 */
int image_batch_read_data(ImageBatch *batch);

/**
 * Clean up image batch data and release resources
 * @param batch Pointer to ImageBatch structure
 * @return status code
 */
int image_batch_cleanup(ImageBatch *batch);

/**
 * Helper function to set up image batch with appropriate storage mode
 * @param batch Pointer to ImageBatch structure to initialize
 * @param storage_mode Storage mode to use
 * @return status code
 */
int image_batch_setup_storage(ImageBatch *batch, StorageMode storage_mode);

/* Release the batch's storage for good (IPC_RMID / unlink). Only after upload
   or quarantine -- never while the batch is still queued. */
int image_batch_release(ImageBatch *batch);

/* STORAGE_HYBRID: the file is the batch's durable form, the shared-memory
   segment a cache of it. */
int image_batch_shm_exists(const ImageBatch *batch);      /* segment present and large enough */
int image_batch_load_to_shm(ImageBatch *batch);           /* file -> new segment, sets shmid */
int image_batch_checkpoint(ImageBatch *batch);            /* segment -> new file (fsync'd), sets filename */

#endif // DIPP_IMAGE_STORE_H