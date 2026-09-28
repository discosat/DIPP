#ifndef DIPP_CONTROL_H
#define DIPP_CONTROL_H

#include <stdint.h>

/* Control plane for the test harness, on the daemon's own SysV message queue
 * (MSG_QUEUE_KEY). Batches arrive as mtype 1; get_message_from_queue receives
 * only that type, so mtype 2 is invisible to the batch path and needs no
 * change to emulate_camera. tools/dipp_ctl.c sends these; the main loop in
 * dipp_process.c drains them once per iteration.
 *
 * Used for trace replay: between imaging bursts the daemon is idle, so the
 * harness advances the battery simulator across the gap instead of waiting
 * it out (the charge model is the same either way), marks each scene as it
 * is fed so the log can be tied to the trace, and can set the stored energy
 * for a sensitivity arm. Not part of the flight interface. */

#define DIPP_CTL_MTYPE 2

typedef enum
{
    CTL_BATTERY_ADVANCE = 1, /* arg_f = simulated seconds to integrate */
    CTL_BATTERY_SET_WH = 2,  /* arg_f = Wh */
    CTL_MARK = 3,            /* arg_i = scene index; logs MARK ts_us= scene= */
} DippControlOp;

typedef struct DippControlMsg
{
    long mtype; /* DIPP_CTL_MTYPE */
    int32_t op;
    int32_t arg_i;
    double arg_f;
} DippControlMsg;

#endif // DIPP_CONTROL_H
