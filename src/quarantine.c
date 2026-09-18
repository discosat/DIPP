#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <errno.h>
#include <sys/stat.h>
#include <unistd.h>
#include "quarantine.h"
#include "image/image_store.h"

static void state_path(const ImageBatch *b, char *out, size_t n)
{
    snprintf(out, n, QUARANTINE_DIR "/%s.failures", b->uuid);
}

static void ensure_dir(void)
{
    if (mkdir(QUARANTINE_DIR, 0755) == -1 && errno != EEXIST)
        printf("QUARANTINE_DIR_ERR %s: %s\n", QUARANTINE_DIR, strerror(errno));
}

static int count_lines(const char *path)
{
    FILE *f = fopen(path, "r");
    if (!f)
        return 0;
    int n = 0, c;
    while ((c = fgetc(f)) != EOF)
        if (c == '\n')
            n++;
    fclose(f);
    return n;
}

int quarantine_record_failure(const ImageBatch *batch, uint32_t error_value)
{
    ensure_dir();
    char path[256];
    state_path(batch, path, sizeof(path));
    FILE *f = fopen(path, "a");
    if (f)
    {
        /* One line per failure: monotonic time, error code (code*1000 +
           pipeline*100 + module index, the daemon's encoding), progress. */
        struct timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        fprintf(f, "%lld %u %d\n", (long long)ts.tv_sec, error_value, batch->progress);
        fflush(f);
        fsync(fileno(f));
        fclose(f);
    }
    int n = count_lines(path);
    printf("QUARANTINE_FAILURE uuid=%s pipeline=%d progress=%d error=%u failures=%d\n",
           batch->uuid, batch->pipeline_id, batch->progress, error_value, n);
    return n;
}

int quarantine_retry_pending(const ImageBatch *batch)
{
    char path[256];
    state_path(batch, path, sizeof(path));
    int n = count_lines(path);
    return n > 0 && n <= QUARANTINE_MAX_RETRIES;
}

void quarantine_clear(const ImageBatch *batch)
{
    char path[256];
    state_path(batch, path, sizeof(path));
    unlink(path);
}

void quarantine_finalize(ImageBatch *batch)
{
    ensure_dir();
    char path[256], rec[256];
    state_path(batch, path, sizeof(path));
    snprintf(rec, sizeof(rec), QUARANTINE_DIR "/%s.txt", batch->uuid);

    /* The downlink record: everything the ground needs to know that a batch
       existed and why it is not coming, in a form the ring uploader can
       ship as-is. The failure lines are appended verbatim. */
    FILE *f = fopen(rec, "w");
    if (f)
    {
        fprintf(f, "uuid=%s\npipeline=%d\nprogress=%d\nnum_images=%d\nbatch_size=%d\n"
                   "storage=%s\nfilename=%s\nretries=%d\n",
                batch->uuid, batch->pipeline_id, batch->progress, batch->num_images,
                batch->batch_size,
                batch->storage_mode == STORAGE_MMAP ? "mmap" : "mem",
                batch->filename, QUARANTINE_MAX_RETRIES);
        FILE *g = fopen(path, "r");
        if (g)
        {
            char line[128];
            int k = 0;
            while (fgets(line, sizeof(line), g))
                fprintf(f, "failure_%d=%s", k++, line);
            fclose(g);
        }
        fflush(f);
        fsync(fileno(f));
        fclose(f);
    }
    unlink(path);

    /* Release what the batch holds: the segment or the file. A quarantined
       batch's data is exactly the input that just killed a module twice, so
       it is not kept -- and until A8 nothing released it at all. */
    image_batch_release(batch);
    printf("QUARANTINED uuid=%s pipeline=%d progress=%d record=%s\n",
           batch->uuid, batch->pipeline_id, batch->progress, rec);
}
