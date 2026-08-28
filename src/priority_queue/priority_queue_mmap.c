/*
 * Shadow-paged persistent priority queue.
 *
 * The queue is never updated in place. The file holds a header in its own
 * sector plus TWO complete slots; a mutation writes the inactive slot, syncs
 * it, then flips a single word in the header and syncs that. A power cut
 * therefore leaves either the old state or the new one, never a half-written
 * mixture: an interrupted slot write is discarded because the header still
 * points elsewhere, and an interrupted header write fails its checksum and
 * falls back to the other slot.
 *
 * Atomicity rests on one assumption worth stating rather than hiding: that a
 * single word within one sector lands entirely or not at all. That is a
 * property of the storage device, not something this code can guarantee, which
 * is why the header is checksummed and both slots are validated on load.
 *
 * The working copy lives on the HEAP, not in the mapping. That keeps the
 * pointer handed to callers stable across a slot flip, and it takes the
 * pthread_mutex_t out of the persisted file -- lock state has no business
 * surviving a reboot.
 */
#include "priority_queue.h"
#include "utils/minitrace.h"
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <string.h>
#include <stdint.h>
#include <sys/stat.h>
#include <errno.h>
#include <stdio.h>

#define PQ_MAGIC 0x53495050u /* "SIPP" */
#define PQ_VERSION 1u
/* Layout granularity, NOT the page size. msync() requires a page-aligned
   address and fails with EINVAL otherwise, so an unaligned slot silently never
   reaches storage -- exactly the failure this file exists to prevent.
   4096 was wrong: it works on an x86 host but the board's page size is larger,
   so every slot msync there returned EINVAL and the queue was never persisted.
   64 KiB is a multiple of every page size Linux uses on aarch64 (4K/16K/64K),
   so slot offsets are aligned whatever the kernel was built with. The cost is
   a 192 KiB file instead of 52 KiB, which is nothing on an 8 GB device.
   This granularity also keeps the header in a region of its own, so flushing a
   slot cannot drag a half-updated header out with it. */
#define PQ_PAGE 65536u
#define PQ_HEADER_BYTES PQ_PAGE

typedef struct PQSlotData
{
    ImageBatch items[MAX_QUEUE_SIZE];
    int32_t size;
} PQSlotData;

#define PQ_SLOT_BYTES ((((sizeof(PQSlotData)) + PQ_PAGE - 1) / PQ_PAGE) * PQ_PAGE)

typedef union PQSlot
{
    PQSlotData d;
    uint8_t _pad[PQ_SLOT_BYTES];
} PQSlot;

typedef struct PQFile
{
    uint32_t magic;
    uint32_t version;
    uint32_t active; /* 0 or 1 */
    uint32_t crc;    /* over magic|version|active */
    uint8_t _pad[PQ_HEADER_BYTES - 16];
    PQSlot slot[2];
} PQFile;

typedef struct PQCtx
{
    PriorityQueue *pq; /* heap working copy, stable for the caller */
    PQFile *map;
    size_t map_bytes;
    char name[64];
} PQCtx;

#define PQ_MAX_QUEUES 4
static PQCtx g_ctx[PQ_MAX_QUEUES];

static PQCtx *ctx_for(PriorityQueue *pq)
{
    for (int i = 0; i < PQ_MAX_QUEUES; i++)
        if (g_ctx[i].pq == pq)
            return &g_ctx[i];
    return NULL;
}

static uint32_t header_crc(const PQFile *f)
{
    /* FNV-1a over the three meaningful header words. Enough to catch a header
       that was only partly written; this is not a security check. */
    uint32_t h = 2166136261u;
    uint32_t v[3] = {f->magic, f->version, f->active};
    const uint8_t *p = (const uint8_t *)v;
    for (size_t i = 0; i < sizeof(v); i++)
    {
        h ^= p[i];
        h *= 16777619u;
    }
    return h;
}

/* A slot is only usable if its size is in range and every live entry is
   plausible. Without this a single corrupted word was trusted straight into
   pq->items[pq->size++], an out-of-bounds write. */
static int slot_is_valid(const PQSlot *s, const char *name, int which)
{
    if (s->d.size < 0 || s->d.size > MAX_QUEUE_SIZE)
    {
        printf("QUEUE_CORRUPT %s slot %d: size=%d out of range [0, %d]\n",
               name, which, s->d.size, MAX_QUEUE_SIZE);
        return 0;
    }
    for (int i = 0; i < s->d.size; i++)
    {
        const ImageBatch *it = &s->d.items[i];
        if (it->batch_size <= 0 || it->num_images <= 0 ||
            it->progress < -1 || it->progress > MAX_QUEUE_SIZE ||
            memchr(it->filename, '\0', sizeof(it->filename)) == NULL ||
            memchr(it->uuid, '\0', sizeof(it->uuid)) == NULL)
        {
            printf("QUEUE_CORRUPT %s slot %d: entry %d fails validation "
                   "(batch_size=%d num_images=%d progress=%d)\n",
                   name, which, i, it->batch_size, it->num_images, it->progress);
            return 0;
        }
    }
    return 1;
}

/* Publish the working copy: write the inactive slot, sync it, then flip the
   header. Ordering is the whole point -- the header must not name a slot until
   that slot is durable. */
static int pq_persist(PriorityQueue *pq)
{
    PQCtx *c = ctx_for(pq);
    if (!c || !c->map)
        return -1;

    uint32_t inactive = c->map->active ? 0u : 1u;
    PQSlot *dst = &c->map->slot[inactive];

    memcpy(dst->d.items, pq->items, sizeof(dst->d.items));
    dst->d.size = pq->size;
    if (msync(dst, PQ_SLOT_BYTES, MS_SYNC) != 0)
    {
        printf("QUEUE_SYNC_FAIL %s slot %u: %s\n", c->name, inactive, strerror(errno));
        return -1;
    }

    c->map->active = inactive;
    c->map->crc = header_crc(c->map);
    if (msync(c->map, PQ_HEADER_BYTES, MS_SYNC) != 0)
    {
        printf("QUEUE_SYNC_FAIL %s header: %s\n", c->name, strerror(errno));
        return -1;
    }
    return 0;
}

int init_pq_mmap(PriorityQueue **pq, char *filename)
{
    if (pq == NULL || filename == NULL)
    {
        printf("Error: provided PriorityQueue** or filename is NULL\n");
        return -1;
    }

    PQCtx *c = NULL;
    for (int i = 0; i < PQ_MAX_QUEUES; i++)
        if (g_ctx[i].pq == NULL)
        {
            c = &g_ctx[i];
            break;
        }
    if (!c)
    {
        printf("Error: too many priority queues\n");
        return -1;
    }

    int fd = open(filename, O_RDWR | O_CREAT, 0666);
    if (fd == -1)
    {
        printf("Failed to open/create queue file: %s (%s)\n", filename, strerror(errno));
        return -1;
    }

    struct stat st;
    int fresh = (fstat(fd, &st) != 0) || ((size_t)st.st_size < sizeof(PQFile));
    if (ftruncate(fd, sizeof(PQFile)) == -1)
    {
        printf("Failed to size queue file: %s (%s)\n", filename, strerror(errno));
        close(fd);
        return -1;
    }

    PQFile *map = mmap(NULL, sizeof(PQFile), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (map == MAP_FAILED)
    {
        printf("Failed to memory map queue file: %s (%s)\n", filename, strerror(errno));
        return -1;
    }

    PriorityQueue *work = calloc(1, sizeof(PriorityQueue));
    if (!work)
    {
        munmap(map, sizeof(PQFile));
        return -1;
    }

    c->map = map;
    c->map_bytes = sizeof(PQFile);
    c->pq = work;
    snprintf(c->name, sizeof(c->name), "%s", filename);

    int header_ok = !fresh && map->magic == PQ_MAGIC && map->version == PQ_VERSION &&
                    map->active <= 1 && map->crc == header_crc(map);

    int loaded = -1;
    if (header_ok)
    {
        uint32_t order[2] = {map->active, map->active ? 0u : 1u};
        for (int i = 0; i < 2 && loaded < 0; i++)
            if (slot_is_valid(&map->slot[order[i]], filename, (int)order[i]))
                loaded = (int)order[i];

        if (loaded < 0)
            printf("QUEUE_CORRUPT %s: neither slot is usable; starting empty\n", filename);
        else if ((uint32_t)loaded != map->active)
            printf("QUEUE_ROLLBACK %s: active slot %u unusable, recovered slot %d\n",
                   filename, map->active, loaded);
    }
    else if (!fresh)
    {
        printf("QUEUE_CORRUPT %s: header invalid (magic=%08x version=%u active=%u); "
               "starting empty\n", filename, map->magic, map->version, map->active);
    }

    if (loaded >= 0)
    {
        memcpy(work->items, map->slot[loaded].d.items, sizeof(work->items));
        work->size = map->slot[loaded].d.size;
    }
    else
    {
        work->size = 0;
    }

    /* Report the real page size once: if it ever exceeds PQ_PAGE the layout
       assumption is broken and msync will fail silently again. */
    long ps = sysconf(_SC_PAGESIZE);
    if (ps > 0 && (PQ_PAGE % (unsigned long)ps) != 0)
        printf("QUEUE_ALIGN_WARN %s: page size %ld does not divide layout "
               "granularity %u; msync will fail\n", filename, ps, PQ_PAGE);

    pthread_mutex_init(&work->lock, NULL);
    *pq = work;

    /* Re-publish so the file is well-formed regardless of how it arrived:
       a fresh file gets a valid header, and a recovered one gets the slot it
       was rolled back to written into the inactive half. */
    if (!header_ok || loaded < 0 || (uint32_t)loaded != map->active)
    {
        map->magic = PQ_MAGIC;
        map->version = PQ_VERSION;
        map->active = 1; /* pq_persist writes slot 0 and flips to it */
        map->crc = header_crc(map);
        msync(map, PQ_HEADER_BYTES, MS_SYNC);
    }
    pq_persist(work);
    return 0;
}

int enqueue_mmap(PriorityQueue *pq, ImageBatch item)
{
    MTR_BEGIN_FUNC();
    pthread_mutex_lock(&pq->lock);

    /* >= not ==: an out-of-range size must not slip past into
       pq->items[pq->size++], which is an out-of-bounds write. */
    if (pq->size >= MAX_QUEUE_SIZE)
    {
        pthread_mutex_unlock(&pq->lock);
        printf("Priority queue is full\n");
        MTR_END_FUNC();
        return -1;
    }

    item.data = NULL; /* pointers never survive a restart */
    pq->items[pq->size++] = item;
    heapifyUp(pq, pq->size - 1);
    pq_persist(pq);

    pthread_mutex_unlock(&pq->lock);
    MTR_END_FUNC();
    return 0;
}

ImageBatch *dequeue_mmap(PriorityQueue *pq)
{
    /* Deliberately NOT traced before the emptiness check. The main loop polls
       both queues at ~1 kHz, so tracing every empty poll produced ~2,000 noise
       events per second -- 76 MB of trace for a two-minute run. */
    pthread_mutex_lock(&pq->lock);

    if (!pq->size)
    {
        pthread_mutex_unlock(&pq->lock);
        return NULL;
    }

    MTR_BEGIN_FUNC();

    ImageBatch *res = malloc(sizeof(ImageBatch));
    if (!res)
    {
        pthread_mutex_unlock(&pq->lock);
        MTR_END_FUNC();
        return NULL;
    }

    *res = pq->items[0];
    pq->items[0] = pq->items[--pq->size];
    heapifyDown(pq, 0);
    pq_persist(pq);

    pthread_mutex_unlock(&pq->lock);
    MTR_END_FUNC();
    return res;
}

/* Record an updated head in place and publish it. */
int commit_head_mmap(PriorityQueue *pq, const ImageBatch *item)
{
    MTR_BEGIN_FUNC();
    pthread_mutex_lock(&pq->lock);
    if (!pq->size)
    {
        pthread_mutex_unlock(&pq->lock);
        MTR_END_FUNC();
        return -1;
    }
    pq->items[0] = *item;
    pq->items[0].data = NULL;
    pq_persist(pq);
    pthread_mutex_unlock(&pq->lock);
    MTR_END_FUNC();
    return 0;
}

int clean_up_pq_mmap(PriorityQueue *pq)
{
    PQCtx *c = ctx_for(pq);
    pthread_mutex_destroy(&pq->lock);
    if (c)
    {
        if (c->map)
            munmap(c->map, c->map_bytes);
        c->map = NULL;
        c->pq = NULL;
    }
    free(pq);
    return 0;
}

PriorityQueueImpl priority_queue_mmap = {
    .init = init_pq_mmap,
    .enqueue = enqueue_mmap,
    .dequeue = dequeue_mmap,
    .peek = peek,
    .peek_copy = peek_copy_generic,
    .commit_head = commit_head_mmap,
    .get_queue_size = get_queue_size,
    .clean_up = clean_up_pq_mmap};
