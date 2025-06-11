#include <stdint.h>
#include <stddef.h>
#include "image_batch.h"

uint32_t murmur3_32(const uint8_t *key, size_t len, uint32_t seed)
{
    uint32_t h = seed;
    const uint32_t c1 = 0xcc9e2d51;
    const uint32_t c2 = 0x1b873593;
    const int r1 = 15;
    const int r2 = 13;
    const uint32_t m = 5;
    const uint32_t n = 0xe6546b64;

    const int nblocks = len / 4;
    const uint32_t *blocks = (const uint32_t *)(key);
    const uint8_t *tail = (const uint8_t *)(key + nblocks * 4);

    for (int i = 0; i < nblocks; i++)
    {
        uint32_t k = blocks[i];
        k *= c1;
        k = (k << r1) | (k >> (32 - r1));
        k *= c2;

        h ^= k;
        h = (h << r2) | (h >> (32 - r2));
        h = h * m + n;
    }

    uint32_t k1 = 0;
    switch (len & 3)
    {
    case 3:
        k1 ^= tail[2] << 16;
    case 2:
        k1 ^= tail[1] << 8;
    case 1:
        k1 ^= tail[0];
        k1 *= c1;
        k1 = (k1 << r1) | (k1 >> (32 - r1));
        k1 *= c2;
        h ^= k1;
    }

    h ^= len;
    h ^= h >> 16;
    h *= 0x85ebca6b;
    h ^= h >> 13;
    h *= 0xc2b2ae35;
    h ^= h >> 16;

    return h;
}

uint32_t murmur3_batch_fingerprint(ImageBatch *batch, uint32_t config_hash)
{
    ImageBatchFingerprint fingerprint;
    fingerprint.num_images = batch->num_images;
    fingerprint.batch_size = batch->batch_size;
    fingerprint.pipeline_id = batch->pipeline_id;

    uint32_t hash = murmur3_32((const uint8_t *)&fingerprint, sizeof(fingerprint), config_hash);

    return hash;
}