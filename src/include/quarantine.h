#ifndef DIPP_QUARANTINE_H
#define DIPP_QUARANTINE_H

#include <stdint.h>
#include "image/image_batch.h"

/* What happens to a batch whose pipeline failed.
 *
 * Until A8 a failed batch was dropped: its data leaked (segment or file),
 * nothing recorded why, and the ground never heard of it. Now:
 *
 *   1st failure  -> the batch stays at the head of its queue and is re-run
 *                   once at the LOWEST effort of every remaining stage
 *                   (quarantine_retry_pending), on the reasoning that the
 *                   cheapest implementation is the best exercised one and
 *                   a transient (memory pressure, a timeout under load) is
 *                   the most likely cause;
 *   2nd failure  -> the batch is quarantined: a small metadata record --
 *                   uuid, pipeline, progress reached, error code of each
 *                   failure, retries -- is written under QUARANTINE_DIR for
 *                   downlink, its storage is released and it leaves the
 *                   queue. The data itself is not kept: it is the one thing
 *                   that has just proven able to kill a module.
 *
 * Retry state lives in a file per uuid, not in ImageBatch: the struct is
 * shared with every module and with emulate_camera, and it is the record
 * format of the durable queues, so changing it is a redeploy of everything.
 * A file survives a reset, which is what makes the retry bound hold across
 * one. */
#define QUARANTINE_DIR "/usr/share/dipp/quarantine"
#define QUARANTINE_MAX_RETRIES 1

/* Record a failure of `batch` (error code = the last one set). Returns the
   number of failures recorded so far for this uuid, including this one. */
int quarantine_record_failure(const ImageBatch *batch, uint32_t error_value);

/* True when the batch has failed before and is due its one retry. */
int quarantine_retry_pending(const ImageBatch *batch);

/* Final disposition: write the downlink record, release the batch's storage.
   The caller removes it from the queue. */
void quarantine_finalize(ImageBatch *batch);

/* Forget the retry state of a batch that went on to complete. */
void quarantine_clear(const ImageBatch *batch);

#endif
