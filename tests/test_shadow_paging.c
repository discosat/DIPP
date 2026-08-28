/*
 * Shadow-paging tests. Exercises the states a power cut can leave behind by
 * writing them into the file directly, which is deterministic where a real cut
 * is a race.
 *
 * Build natively; the queue code is plain POSIX.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include "priority_queue.h"

#define PQ_HEADER_BYTES 65536u
#define PATH "/tmp/sipp_shadow_test.queue"

static int failures = 0;
static void check(const char *what, int ok)
{
    printf("  %-52s %s\n", what, ok ? "PASS" : "FAIL");
    if (!ok) failures++;
}

static ImageBatch mkbatch(int i)
{
    ImageBatch b;
    memset(&b, 0, sizeof(b));
    b.mtype = 1;
    b.num_images = 1;
    b.batch_size = 1000 + i;
    b.pipeline_id = 2;
    b.priority = 1000000LL * (10 - i);   /* lower value sorts first */
    b.progress = -1;
    snprintf(b.uuid, sizeof(b.uuid), "uuid-%08d", i);
    snprintf(b.filename, sizeof(b.filename), "/usr/share/dipp/data/batch_%d.bin", i);
    return b;
}

/* Header layout mirrors the implementation. */
static uint32_t read_u32(long off)
{
    FILE *f = fopen(PATH, "rb"); uint32_t v = 0;
    fseek(f, off, SEEK_SET); if (fread(&v, 4, 1, f) != 1) v = 0; fclose(f);
    return v;
}
static void write_u32(long off, uint32_t v)
{
    FILE *f = fopen(PATH, "r+b"); fseek(f, off, SEEK_SET); fwrite(&v, 4, 1, f); fclose(f);
}
static long slot_offset(uint32_t which, size_t slot_bytes)
{
    return (long)PQ_HEADER_BYTES + (long)which * (long)slot_bytes;
}

int main(void)
{
    PriorityQueueImpl *impl = &priority_queue_mmap;
    PriorityQueue *pq = NULL;
    unlink(PATH);

    printf("shadow paging tests\n");

    /* 1. fresh queue persists and recovers */
    if (impl->init(&pq, PATH) != 0) { printf("init failed\n"); return 1; }
    for (int i = 0; i < 3; i++) impl->enqueue(pq, mkbatch(i));
    check("3 enqueued", impl->get_queue_size(pq) == 3);
    impl->clean_up(pq); pq = NULL;

    if (impl->init(&pq, PATH) != 0) { printf("reinit failed\n"); return 1; }
    check("recovered 3 after clean restart", impl->get_queue_size(pq) == 3);

    /* Work out the on-disk geometry from the file itself. */
    FILE *f = fopen(PATH, "rb"); fseek(f, 0, SEEK_END); long fsz = ftell(f); fclose(f);
    size_t slot_bytes = (size_t)((fsz - PQ_HEADER_BYTES) / 2);
    uint32_t active = read_u32(8);
    printf("  [file %ld B, slot %zu B, active slot %u]\n", fsz, slot_bytes, active);

    /* 2. a torn write to the INACTIVE slot must be harmless: the header still
          names the good one. */
    impl->clean_up(pq); pq = NULL;
    uint32_t inactive = active ? 0u : 1u;
    long size_off = (long)(MAX_QUEUE_SIZE * sizeof(ImageBatch)); /* size follows items */
    write_u32(slot_offset(inactive, slot_bytes) + size_off, 0x7FFFFFFF); /* wreck its size */
    if (impl->init(&pq, PATH) != 0) { printf("init failed\n"); return 1; }
    check("torn INACTIVE slot ignored, queue intact", impl->get_queue_size(pq) == 3);
    impl->clean_up(pq); pq = NULL;

    /* 3. a torn write to the ACTIVE slot must ROLL BACK to the other one.
          This is the property shadow paging exists for. */
    active = read_u32(8);
    write_u32(slot_offset(active, slot_bytes) + size_off, 0x7FFFFFFF);
    if (impl->init(&pq, PATH) != 0) { printf("init failed\n"); return 1; }
    check("torn ACTIVE slot rolls back to the other slot",
          impl->get_queue_size(pq) == 3);
    impl->clean_up(pq); pq = NULL;

    /* 4. a torn header must be detected, not trusted. */
    write_u32(12, 0xDEADBEEF); /* crc */
    if (impl->init(&pq, PATH) != 0) { printf("init failed\n"); return 1; }
    check("torn header detected, daemon survives", impl->get_queue_size(pq) == 0);
    impl->clean_up(pq); pq = NULL;

    /* 5. after any recovery the file must be usable again. */
    if (impl->init(&pq, PATH) != 0) { printf("init failed\n"); return 1; }
    impl->enqueue(pq, mkbatch(42));
    check("queue usable again after recovery", impl->get_queue_size(pq) == 1);
    impl->clean_up(pq); pq = NULL;
    if (impl->init(&pq, PATH) != 0) { printf("init failed\n"); return 1; }
    check("that entry survived a restart", impl->get_queue_size(pq) == 1);
    impl->clean_up(pq);

    unlink(PATH);
    printf(failures ? "\nFAILED (%d)\n" : "\nALL PASS\n", failures);
    return failures ? 1 : 0;
}
