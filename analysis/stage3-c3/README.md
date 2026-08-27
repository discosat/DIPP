# C3 smoke test — 20 SIGKILL/restart cycles

Pipeline 2 (static low), 20 batches, 20 kill cycles, on the board 2026-08-27.
Batches identified by UUID at both ends, so loss and duplication are countable.
Script: `tests/board/c3_smoke.sh`.

## Run 1 — peek-process-commit only

| | |
|---|---|
| fed | 20 |
| completed (unique) | 11 |
| **lost** | **9** |
| duplicated | 0 |

**FAILED the gate.** Keeping the batch queued during processing was necessary
but not sufficient.

### Root cause

Progress was committed per **batch**; files were consumed per **module**.
`finalize()` in each module unlinked its predecessor as soon as that module
finished, while the queue entry's `(progress, filename)` was only updated when
the whole pipeline returned. That left a window spanning the entire pipeline in
which the queue named a file the first module had already deleted.

A kill inside that window stranded the batch: on restart the first module could
not open its input, raised `MMAP_OPEN`, `process()` reported FAILED, and the
batch was dropped.

Evidence: the data directory held **zero** files while both queues still had
entries, and every error was logged at module 0 with `progress = -1` — i.e. a
batch that had never advanced, pointing at an ingest file that was gone.

## Run 2 — with per-module checkpointing

Modules no longer unlink their input; the parent commits `(progress, filename)`
after every module and only then removes the predecessor.

| | |
|---|---|
| fed | 20 |
| completed (unique) | 18 |
| duplicated | **0** |
| dropped as FAILED | **0** |
| zero-fill fabrication attempts | 0 |

The loss mechanism is gone — `dropped=0` means the FAILED path never fired
once, against 9 drops in run 1. Duplication stayed at zero throughout, which is
what the commit ordering is designed to guarantee.

## Status: NOT yet passing

Two of twenty batches are still unaccounted for. The gate is "zero lost, zero
duplicated" and this is 18/20. The test harness also died during its own drain
phase, so the run did not self-report and the final two were not traced to a
cause. **Do not treat Stage 3 as complete.**

Next step is to account for those two specifically: instrument the drain, and
check whether they are stranded in a queue, dropped somewhere unlogged, or
simply never finished within the drain window.

## Test harness notes

- `pidof` returns a SPACE-SEPARATED list, so `kill "$(pidof dipp)"` passes it as
  one bogus argument and silently fails, letting daemons accumulate and quietly
  invalidating the run. Use `pkill`. This invalidated a first attempt.
- `pidof` does not match shell scripts (it matches the interpreter), so it
  cannot be used to test whether the harness is still alive; use `ps`.
- Forked module children share the executable name, so `pidof dipp` legitimately
  returns several PIDs during processing.

## The two unaccounted batches — traced

They failed for **different** reasons, and neither is the mechanism fixed above.

### 1. `c9ff2224-…` — never reached the daemon at all

Its UUID appears nowhere in the daemon log. It was fed successfully by
`emulate_camera`, so the message reached the SysV queue, but the daemon never
recorded it.

`get_message_from_queue` does `msgrcv` — which **removes** the message from the
kernel queue — and only then `enqueue`s into the durable priority queue. A kill
between those two loses the batch outright: gone from the kernel queue, never
written to the persisted one.

This is a genuine gap and it lies **outside** the priority queue's durability
entirely, so no amount of queue-commit ordering fixes it. It is also not
trivially fixable: SysV message queues have no peek-then-commit, so the options
are to accept and document the window, to have the producer retain and re-send
until acknowledged, or to replace the ingest IPC.

Note the return value of `image_batch_setup_storage()` — which is what persists
the data — is currently **discarded** in `get_message_from_queue`, so an ingest
that fails to persist is enqueued anyway. That is a separate defect on the same
path and should be fixed regardless.

### 2. `6b7cb736-…` — progressed to 3 of 5, then vanished

Traced through the log: modules 0–3 executed (demosaic, tile, classification,
segmentation), each with an EXEC record and an energy debit, reaching
`progress=3` with `ingest_depth=1 partial_depth=9`. It was then reported
`Pipeline partially executed successfully … progress=3` — and never appears
again. It was not dropped (`dropped=0`), not completed, and is not in either
queue now.

Still open. The next step is to instrument every queue removal with UUID and
reason, so a batch cannot leave a queue unlogged.

## Bottom line

The per-module checkpointing fix is sound and eliminated the mechanism that
lost 9 batches. What remains is **two different, smaller holes** — one at the
ingest IPC boundary that the queue design cannot cover, one still undiagnosed.
Stage 3's gate stays open.
