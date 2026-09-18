/* Reclaim leaked SysV shared-memory segments. The board's BusyBox has no
 * ipcrm/ipcs, and DIPP's STORAGE_MEM path detaches without IPC_RMID, so
 * segments accumulate across runs until allocation fails and the first module
 * of the pipeline (demosaic, which carries the full-resolution frame) crashes
 * with MODULE_EXIT_CRASH. Run between benchmark configs to keep each
 * measurement on an unpolluted machine. */
#include <sys/ipc.h>
#include <sys/shm.h>
#include <stdio.h>

int main(void)
{
    struct shmid_ds ds;
    int maxid = shmctl(0, SHM_INFO, &ds);
    if (maxid < 0) { perror("SHM_INFO"); return 1; }

    int removed = 0, failed = 0;
    unsigned long bytes = 0;
    for (int i = 0; i <= maxid; i++) {
        struct shmid_ds seg;
        int shmid = shmctl(i, SHM_STAT, &seg);
        if (shmid < 0) continue;              /* hole in the index */
        if (shmctl(shmid, IPC_RMID, NULL) == 0) {
            removed++; bytes += (unsigned long)seg.shm_segsz;
        } else {
            failed++;
        }
    }
    printf("SHMCLEAN removed=%d failed=%d bytes=%lu\n", removed, failed, bytes);
    return 0;
}
