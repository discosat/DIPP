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

#endif // DIPP_IMAGE_STORE_H