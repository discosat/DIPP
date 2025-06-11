#ifndef MURMUR_HASH_H
#define MURMUR_HASH_H

#include <stdint.h>
#include <stddef.h>
#include "image_batch.h"

uint32_t murmur3_32(const uint8_t *key, size_t len, uint32_t seed);
uint32_t murmur3_batch_fingerprint(ImageBatch *batch, uint32_t config_hash);

#endif // MURMUR_HASH_H