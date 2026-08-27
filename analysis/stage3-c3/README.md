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
