/* dipp_ctl -- harness control tool.
 *
 *   dipp_ctl advance <seconds>   integrate that many simulated seconds now
 *   dipp_ctl set-wh  <Wh>        set the stored energy
 *   dipp_ctl mark    <scene>     log MARK ts_us= scene= ...
 *
 * Sends a DippControlMsg (mtype 2) on the daemon's own SysV message queue.
 * Batches arrive on the same queue as mtype 1 and get_message_from_queue
 * receives only that type, so these are invisible to the batch path and no
 * CSP client is needed on the board. See src/include/dipp_control.h.
 *
 * Used by run.sh during trace replay: the daemon is idle across the gap
 * between two imaging bursts, so the battery is advanced across it instead of
 * the gap being waited out. Not part of the flight interface.
 *
 * A SysV queue outlives the process that created it (verified on the board:
 * key 71 persists after the daemon exits). So "the queue exists" does not
 * mean "the daemon is running", and a message sent between runs would be read
 * by the NEXT daemon -- a stale fast-forward corrupting that run's battery.
 * Hence: refuse unless a process named dipp is running (exit 2), never create
 * the queue (no IPC_CREAT), and the daemon itself discards any control
 * message already waiting when it starts (CTL_STALE_DISCARDED).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <ctype.h>
#include <dirent.h>
#include <sys/ipc.h>
#include <sys/msg.h>

#include "dipp_control.h"
#include "dipp_process.h"

/* Is a process whose name is exactly "dipp" running? BusyBox has pidof, but
   this must not depend on a shell. */
static int daemon_running(void)
{
    DIR *d = opendir("/proc");
    if (!d)
        return 0;
    struct dirent *e;
    int found = 0;
    while (!found && (e = readdir(d)) != NULL)
    {
        if (!isdigit((unsigned char)e->d_name[0]))
            continue;
        char path[288], comm[64] = {0};
        snprintf(path, sizeof(path), "/proc/%s/comm", e->d_name);
        FILE *f = fopen(path, "r");
        if (!f)
            continue;
        if (fgets(comm, sizeof(comm), f))
        {
            comm[strcspn(comm, "\n")] = '\0';
            found = strcmp(comm, "dipp") == 0;
        }
        fclose(f);
    }
    closedir(d);
    return found;
}

static int usage(void)
{
    fprintf(stderr,
            "usage: dipp_ctl advance <seconds> | set-wh <Wh> | mark <scene>\n");
    return 1;
}

int main(int argc, char *argv[])
{
    if (argc != 3)
        return usage();

    DippControlMsg m;
    memset(&m, 0, sizeof(m));
    m.mtype = DIPP_CTL_MTYPE;

    const char *op = argv[1];
    if (strcmp(op, "advance") == 0)
    {
        m.op = CTL_BATTERY_ADVANCE;
        m.arg_f = atof(argv[2]);
        if (!(m.arg_f > 0.0))
        {
            fprintf(stderr, "dipp_ctl: advance needs a positive number of seconds\n");
            return 1;
        }
    }
    else if (strcmp(op, "set-wh") == 0)
    {
        m.op = CTL_BATTERY_SET_WH;
        m.arg_f = atof(argv[2]);
        if (!(m.arg_f >= 0.0))
        {
            fprintf(stderr, "dipp_ctl: set-wh needs a non-negative number of Wh\n");
            return 1;
        }
    }
    else if (strcmp(op, "mark") == 0)
    {
        m.op = CTL_MARK;
        m.arg_i = atoi(argv[2]);
    }
    else
    {
        return usage();
    }

    if (!daemon_running())
    {
        fprintf(stderr, "dipp_ctl: dipp is not running; not sending (the next daemon would read it)\n");
        return 2;
    }
    int q = msgget(MSG_QUEUE_KEY, 0);
    if (q == -1)
    {
        fprintf(stderr, "dipp_ctl: no message queue (key %d): %s\n", MSG_QUEUE_KEY, strerror(errno));
        return 2;
    }

    if (msgsnd(q, &m, sizeof(m) - sizeof(long), 0) == -1)
    {
        fprintf(stderr, "dipp_ctl: msgsnd: %s\n", strerror(errno));
        return 2;
    }

    printf("CTL op=%s arg_i=%d arg_f=%.3f\n", op, m.arg_i, m.arg_f);
    return 0;
}
