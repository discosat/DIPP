#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include "cost_store.h"
#include <stdio.h>
#include <string.h>
#include <errno.h>

/* Persistent store: the file is the store. A file with a valid format-2
   header (a seed, or what a previous run left) is used as-is; anything else
   -- absent, short, format 1 -- is reset to an empty format-2 image. */
int cost_store_init_mmap(CostStore **store, char *filename)
{
    const char *file = filename ? filename : CACHE_FILE;
    size_t cache_size = sizeof(CostStore);
    struct stat st;

    if (store == NULL)
    {
        printf("Error: provided CostStore** is NULL\n");
        return -1;
    }

    int exists = (stat(file, &st) == 0);
    off_t old_size = exists ? st.st_size : 0;

    int fd = open(file, O_RDWR | O_CREAT, 0644);
    if (fd < 0)
    {
        printf("Error opening/creating cache file: %s (%s)\n", file, strerror(errno));
        return -1;
    }
    if (ftruncate(fd, (off_t)cache_size) == -1)
    {
        printf("Error resizing cache file: %s (%s)\n", file, strerror(errno));
        close(fd);
        return -1;
    }

    void *mapped = mmap(NULL, cache_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (mapped == MAP_FAILED)
    {
        printf("Error mapping cache file: %s (%s)\n", file, strerror(errno));
        close(fd);
        return -1;
    }
    close(fd);
    *store = (CostStore *)mapped;

    int loaded = 0;
    if (exists && old_size == (off_t)cache_size && cost_store_header_ok(*store))
        loaded = 1;
    else
    {
        printf("COST_STORE_RESET file=%s reason=%s\n", file,
               !exists ? "absent" : old_size != (off_t)cache_size ? "wrong_size" : "bad_header");
        cost_store_reset(*store);
        msync(*store, cache_size, MS_SYNC);
    }

    int n = 0;
    for (int i = 0; i < MAX_ENTRIES; i++)
    {
        if ((*store)->items[i].valid)
        {
            n++;
            if ((*store)->items[i].timestamp > global_time)
                global_time = (*store)->items[i].timestamp;
        }
    }
    printf("COST_STORE_INIT backend=mmap file=%s loaded=%d entries=%d\n", file, loaded, n);
    return 0;
}

int put_mmap(CostStore *store, const CostEntry *entry)
{
    int idx = cost_store_upsert(store, entry);
    if (idx != -1)
        msync(store, sizeof(CostStore), MS_SYNC);
    return idx;
}

int clean_up_mmap(CostStore *store)
{
    if (store)
        munmap(store, sizeof(CostStore));
    return 0;
}

CostStoreImpl cost_store_mmap = {
    .init = cost_store_init_mmap,
    .put = put_mmap,
    .clean_up = clean_up_mmap};
