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
 * Deliberately does NOT pass IPC_CREAT: if the daemon is not running there is
 * nothing to control, and creating the queue here would leave a stray one for
 * the next daemon to inherit. Exit 2 says so.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/ipc.h>
#include <sys/msg.h>

#include "dipp_control.h"
#include "dipp_process.h"

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

    int q = msgget(MSG_QUEUE_KEY, 0);
    if (q == -1)
    {
        fprintf(stderr, "dipp_ctl: no message queue (key %d): %s -- is dipp running?\n",
                MSG_QUEUE_KEY, strerror(errno));
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
