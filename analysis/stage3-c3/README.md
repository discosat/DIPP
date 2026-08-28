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

### 1. `c9ff2224-…` — never reached the daemon: OUT OF SCOPE

Its UUID appears nowhere in the daemon log. It was fed successfully by
`emulate_camera`, so the message reached the SysV queue, but the daemon never
recorded it.

`get_message_from_queue` calls `msgrcv`, which **removes** the message from the
kernel queue, and only then enqueues into the durable priority queue. A kill
between those two loses the batch: gone from the kernel queue, never written to
the persisted one.

**This is deliberately out of DIPP's scope.** DIPP's durability contract begins
when a batch is *accepted into its queue*; getting a batch as far as that queue
belongs to the producer — on the flight system, the camera control software,
which owns the image until DIPP acknowledges it. SysV message queues offer no
peek-then-commit, so no change inside DIPP can close the window; only a
producer that retains and re-sends until acknowledged can.

State the boundary explicitly in W4/W5 rather than leaving it implicit: *DIPP
guarantees no loss of any batch it has accepted; delivery to DIPP is the
producer's responsibility.* That is a normal interface contract, and it makes
the persistence claim precise instead of overreaching.

**One part of this path WAS in scope and is fixed.** The return value of
`image_batch_setup_storage()` was discarded, so a batch whose data failed to
persist was enqueued anyway and then failed later at module 0, looking exactly
like a durability bug. It is now rejected at ingest with an `INGEST_REJECT` log
line, which keeps the queue's invariant honest: everything in it has its data on
storage.

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


## Run 3 — with full queue instrumentation

Same shape: 20 batches, 20 kill cycles, pipeline 2.

| | |
|---|---|
| fed | 20 |
| completed (unique) | 18 |
| duplicated | **0** |
| queue_mismatches | **0** |
| removes by reason | 18 complete, 18 moved_to_partial, **0 failed** |
| ingest_rejects | 0 |
| never seen by daemon | **0** (hole 1 did not fire this run) |
| seen but incomplete | 2 |

`queue_mismatches=0` across every run rules out a heap-ordering bug: `dequeue()`
has never removed a batch other than the one just processed.

### The orphan scan is EXONERATED

The leading hypothesis was that `reclaim_orphaned_batch_files()` was deleting
files a queued batch still needed. It is not. Comparing every `ORPHAN_REMOVE`
path against every `ORPHAN_SCAN_REF` filename:

    removed_paths=13  referenced_paths=28  overlap=0

Zero intersection. The scan has never removed a referenced file.

### Two corrections to earlier readings in this file

1. **"kept 0 referenced" did not mean the scan was misbehaving.** It means no
   file on disk matched a queue entry at that moment, which is the normal state
   when the queues are empty.
2. **"No orphan-scan line at startup" does not mean the daemon is stuck.** The
   scan only logs `if (removed || kept)`, so a clean start with empty queues and
   an empty data directory logs nothing at all. A bisect that started the daemon
   with fresh queue files and saw no scan line therefore proved nothing; thread
   states confirmed `process_images_loop` was simply idling in its `usleep`
   poll, not deadlocked.

### What is actually established

The last observed state of a lost batch is consistent across runs: selected from
the partial queue at `progress=3`, the daemon killed during module 4, and
afterwards the batch is not in either queue and never completes. It is not
removed by any logged path — `QUEUE_REMOVE` accounts for exactly 36 removals,
all of them `complete` or `moved_to_partial`.

So a batch leaves the partial queue without going through `dequeue()`. The next
instrumentation step is to log both queue sizes at every startup and after every
`commit_head`, which distinguishes "the entry was never written durably" from
"the entry was written and later lost".

**Stage 3 gate remains open at 18/20**, with the systemic 9-batch loss fixed and
duplication at zero throughout.

## Incidental finding: the system starts exactly at its own refusal boundary

Trying to build a fast repro surfaced this, and it matters well beyond C3.

`BATTERY_SAFETY_MARGIN_WH` is **64.4 Wh**. Initial SoC is `0.7 × 92 Wh` =
**exactly 64.4 Wh**. The best-effort and lowest-effort heuristics both compute

    energy_requirement = (battery_level_wh - BATTERY_SAFETY_MARGIN_WH) * 1e6

so at startup that is **zero**, and `judge_implementation` /
`get_default_implementation` refuse *every* module until the simulated battery
charges above the margin. Net power in sunlight is 25.4 − 13.07 = +12.3 W, so
the budget grows quickly, but for the first seconds after start the scheduler
declines all work and parks batches as partial.

Consequences:

- **This is the only regime in which partials occur** in a short run. Feeding
  batches 2 s after startup lets the battery gain ~6,800 µWh of headroom —
  more than an entire low-effort pipeline (1,931 µWh) — so every batch runs
  straight through and no batch is ever parked. That is why two probe attempts
  produced zero partial events.
- **C7a's initial-SoC sweep {30, 50, 70, 90}% is not neutral.** 70% sits exactly
  on the boundary, so that arm measures the transient rather than a steady
  state, and the three other arms are not symmetric around it. Either sweep
  relative to the margin or state the coincidence explicitly.
- **Any run's first seconds are unrepresentative.** The published runs began
  feeding immediately after launch, so their early batches were scheduled under
  a near-zero energy budget. A warm-up discard is needed for the same reason
  C4 discards the first batch for latency.

Neither constant is wrong on its own — 64.4 Wh is 70% of 92 Wh, which is the
documented margin — but their coincidence makes the start of every run a
special case, and nothing in the manuscript says so.

## Final run — the gate is met

20 batches, 20 SIGKILL/restart cycles, pipeline 2, with stdout line-buffered
and the classifier corrected.

| | |
|---|---|
| fed | 20 |
| reached DIPP's queue | 19 |
| completed | 19 |
| **lost, in scope** | **0** |
| **duplicated** | **0** |
| never reached the queue (out of scope) | 1 |

**Every batch DIPP accepted into its queue completed exactly once, across 20
kill cycles.** The one that did not was lost in the ingest window — `msgrcv`
consumed the message and `image_batch_setup_storage` persisted the data, then
SIGKILL landed before `enqueue`. Its only trace in the whole log is the
`ORPHAN_REMOVE` line where the startup scan correctly reclaimed the stranded
file. That is the producer-side boundary recorded above, not a DIPP defect.

## Two measurement bugs had to be fixed first, and both were mine

Neither of the earlier "losses" was real.

**1. stdout block buffering destroyed the evidence.** `c3_smoke.sh` runs the
daemon without `-t`, so tracing is off, so `mtr_register_sigint_handler()` is
never called and SIGTERM keeps its default disposition — the process dies
without flushing. stdout is redirected to a file, hence 4 KB block-buffered, so
up to a full block of already-printed lines was discarded on every kill,
including `Pipeline fully executed` and `QUEUE_REMOVE`. Completed batches
therefore looked like they had vanished without a trace. Fixed with
`setvbuf(stdout, NULL, _IOLBF, 0)`.

This also twice made an idling daemon look stuck during initialisation: the
unconditional `STARTUP_QUEUES` line was sitting in the buffer. Feeding a single
batch forced a `fork()`, which ran the pre-fork `fflush(NULL)`, and 214 lines
appeared at once.

**2. The classifier counted an `ORPHAN_REMOVE` line as evidence the daemon had
seen the batch.** It greps the log for the UUID, and that line names the
batch's data file — so a batch that never reached a queue was reported as
"seen but incomplete", i.e. an in-scope loss. It now classifies against queue
events only (`QUEUE_SELECT`, `QUEUE_REMOVE`, `EXEC`).

The irony is not lost: this revision exists partly because the original traces
were invalid, and the same class of defect — an instrument discarding its own
output — cost three runs here before it was spotted.

## Queue-corruption fault injection, with negative control

The torn-write question ("do power cuts corrupt the queue?") needs real power
cuts. But the more decisive question for the paper — **would we survive one?** —
is testable now by writing corruption straight into the queue file.

Injected an out-of-range `size` (65535, against `MAX_QUEUE_SIZE` 100) at offset
20800 of `/usr/share/dipp/queue_file`, then started the daemon.

| Build | Result |
|---|---|
| **Before the fix** (`HEAD~1`) | **SIGSEGV** — kernel audit: `comm="dipp_noval" sig=11 res=1`; `QUEUE_CORRUPT` lines: 0 |
| **After the fix** | detected, logged, daemon survives |

After:

```
QUEUE_CORRUPT /usr/share/dipp/queue_file: size=65535 out of range [0, 100]
QUEUE_CORRUPT /usr/share/dipp/queue_file: discarding queue contents
STARTUP_QUEUES ingest=0 partial=0
daemon=alive
```

So a single corrupted word used to crash the daemon on startup — and because
the queue file persists, it would crash it again on **every** restart. That is
an unrecoverable boot loop from one bad word, on a satellite. It is now a
logged, recoverable event.

Note the queue file is 20,856 bytes: 100 x sizeof(ImageBatch) (208, after
`priority` became `int64_t`) + 56.

**This is not shadow paging.** It bounds the blast radius of a corrupt queue; it
does not make the queue atomic across a power cut, and it does not tell us
whether power cuts produce corruption in the first place.

## Otii status — programmable power cuts are close but blocked

The Otii Ace is a source-measure unit, not just a meter: the existing
`energy_measurement_csh.py` configures it as a 5 V / 4 A supply
(`set_main_voltage`, `set_max_current`). So power cuts can be scripted, without
physical access to the board.

Present and working: `otii_tcp_client` 1.0.11, the Otii 3 desktop app running,
TCP API on 127.0.0.1:1905, and login now succeeds.

**Blocked on licensing:** `get_licenses()` returns **0**, so `get_devices()`
fails with "No license". The TCP automation API needs an Automation Toolbox
licence that this account does not currently hold. Manual use of the Otii UI
does not require it, so toggling the main output by hand in the UI remains a
fallback for a modest number of cuts.

Also still to confirm: whether the board is actually powered from the Otii main
channel, or from a separate supply. The script sets 5 V/4 A, but that is intent,
not proof of wiring.

## C3 with REAL power cuts — the test SIGKILL could not do

20 batches, **20 genuine power cuts** via the Otii (which supplies the board at
5 V), pipeline 2. Each cut cold-boots the board, so every cycle re-mounts
`/mnt/flash` and restarts the daemon; batches are tracked by UUID across boots.

| | |
|---|---|
| fed | 20 |
| completed | 20 |
| **lost, in scope** | **0** |
| **duplicated** | **0** |
| **`QUEUE_CORRUPT` detections** | **0** |
| daemon starts recorded | 14 |

**No torn write was observed in 20 power cuts.** Combined with the validation
now on the load path, that is the empirical basis for deferring shadow paging:
the queue survived real power loss intact, and had it not, corruption would have
been detected rather than crashing the daemon.

Two honest caveats:

- **20 cuts is a small sample for a rare event.** This bounds the rate loosely,
  it does not show torn writes are impossible. The cuts also land at arbitrary
  points, so they are unlikely to hit the narrow window mid-`msync` where the
  20,856-byte structure is partially written. A targeted version — cut power
  deliberately during a queue `msync` — would be a much sharper test and is
  cheap now the rig exists.
- **`boots=14` against 20 cuts.** Fewer daemon starts were logged than cuts, so
  on some cycles the daemon did not start (the harness does not yet verify it
  came up). That weakens the run: some cuts landed on an idle board rather than
  one mid-pipeline. Worth fixing before the Stage 5 200-trial campaign.

## Shadow paging under real power cuts — final result

20 batches, **20 verified power cuts** (the Otii asserts the output actually
went off before sleeping), pipeline 2, with shadow paging in place.

| | |
|---|---|
| fed | 20 |
| completed, unique | **20** |
| **lost, in scope** | **0** |
| **duplicated** | **0** |
| never reached the queue | 0 |
| `QUEUE_ROLLBACK` | **0** |
| `QUEUE_CORRUPT` | 0 |
| `QUEUE_SYNC_FAIL` | 0 |
| daemon starts / cuts | 19 / 20 |

**Every batch completed exactly once across 20 real power cuts.** Even the
ingest-window loss that appeared in earlier runs did not occur here.

### What this says about shadow paging

`QUEUE_ROLLBACK` is **0**: with detection in place and 20 real cuts, **no torn
write was ever observed**. Both campaigns agree — the pre-shadow-paging run also
saw no corruption. So on this hardware, at this cut rate, torn writes are rare
enough not to appear in 40 cuts.

That does not make the mechanism pointless, but it does change the claim W5 can
make. Honest framing: *the queue is validated on load, survives real power
loss, and rolls back to the previous slot if a write is ever torn; across 40
power cuts no torn write was observed.* That is a measured statement rather than
an argued one, which is what the mechanism was built to buy.

The cost measured out as predicted: no `QUEUE_SYNC_FAIL`, no latency or
completion regression against the pre-shadow-paging runs.

### Platform observation worth recording separately

**One power cut left the board hung.** After the campaign it drew 3.11 W --
powered and running -- but emitted nothing on the console, and a capture from
the instant of the next cut showed it stopping mid-U-Boot after 2,064 bytes. A
further power cycle booted it cleanly (33,766 bytes, full systemd boot to a
login prompt in 20 s).

So the board itself intermittently fails to boot after a power cut. That is a
platform/bootloader behaviour, not DIPP's, but it belongs in W4/W5: *system
survival* and *data preservation* are separate claims, and DIPP preserved every
batch across cuts that the platform sometimes did not survive cleanly. It also
matters operationally -- the flight system's PDU watchdog (SI-DISCOII-DR-001
5.5) exists for exactly this.

`boots=19` across 20 cuts: the harness caught one cycle where the daemon did not
start, which is why it now verifies startup rather than assuming it.
