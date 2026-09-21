# Stage 5 — Campaigns

## A8 — fault tolerance (2026-09-18)

Six defects in how the daemon handled a failing module, each fixed and then
exercised by injecting every fault class on the board
(`tools/fault_inject.py`, `fault_inject.json`).

**What was wrong.**

1. **No timeout.** `module_timeout` is a CSP parameter that reads 0 on a fresh
   image, and `alarm(0)` cancels rather than schedules, so `MODULE_EXIT_TIMEOUT`
   had never fired and a hung module would have hung the daemon. The manifest
   also documented the field as microseconds while `alarm()` takes seconds
   (30 000 000 would have been a 347-day timeout). Now: `module_timeout: 30`
   (seconds) in the manifest, and the daemon floors 0 to 30 s. The bound has to
   exceed the slowest legitimate stage — JPEG XL e5 at 20.3 s per frame.
2. **A silent child blocked the daemon forever.** The result and error pipes
   were created once per pipeline and the parent kept both ends of both open,
   so a child that died without writing an error code left `read()` waiting on
   a pipe the parent itself held. Pipes are now per execution; the parent
   closes its write ends after the fork; a silent child yields EOF.
3. **A failed batch was dropped**, its storage leaked and nothing recorded why.
   Now (`src/quarantine.c`): the first failure leaves the batch at the head of
   its queue and re-runs it once with every remaining stage at the **lowest
   effort**; the second failure quarantines it — a record with uuid, pipeline,
   progress, the error code of each failure and the retry count is written to
   `/usr/share/dipp/quarantine/<uuid>.txt` for downlink, the segment or file is
   released, the batch leaves the queue. Retry state is a file per uuid, not a
   field in `ImageBatch` (which is shared with every module and is the queue's
   record format), so it survives a reset and the bound holds across one.
4. **Every completed batch leaked its final SHM segment** (`image_batch_cleanup`
   only detached; the last module reuses the largest segment the pipeline
   touched — up to 30 MB — so 61 segments held 1.59 GB of 1.95 GB after one
   session). `image_batch_release` now IPC_RMIDs / unlinks after upload and
   after quarantine.
5. **The child's result was trusted.** One `read()` accepted a short struct,
   `strcpy` copied child-controlled `uuid`/`filename`, and `num_images`,
   `batch_size`, `shmid` were taken as given. Now the result is read in full and
   `validate_module_result` rejects a changed uuid/pipeline/priority, a
   progress that is not the module's index, negative or inconsistent sizes, an
   unterminated or out-of-directory filename, a non-empty MEM batch without a
   segment (`MODULE_RESULT_INVALID`, 603). On the module side every result is
   zero-initialised with the input's storage identity, since untouched fields
   used to be stack garbage.
6. `shmat` fails with `(void*)-1`, not NULL — fixed in the modules'
   `memory_util.c` (the daemon's copy was fixed in Stage 3).

**The fault module itself was mostly a no-op.** Built at `-O3`, the compiler
deleted the divide-by-zero, the null stores, the out-of-bounds index, the
double free and the buffer overflow as undefined behaviour and compiled the
recursion to an infinite loop. `crash_and_burn` is now built at `-O0` with
volatile operands and every case reaches its faulting instruction (checked in
the disassembly of `module()`: `sdiv`, the stores, `blr` on a null function
pointer, the recursion call, `memcpy`/`memset`, `free` twice). Two cases cannot
fault by themselves on this platform and say so: AArch64 integer division by
zero does not trap (`sdiv` yields 0), so the arithmetic case raises `SIGFPE`
after the division; `malloc(1 TB)` returns NULL under overcommit, so that case
reports through the error pipe — the graceful path, deliberately distinct from
a crash. A `hang` case (13th) exercises the timeout.

**Injected on the board** (`cs12_test_matched_00`, pipeline demosaic → tile →
crash_and_burn, one batch per case; expected: fail, retry at lowest effort,
fail, quarantine; no leaked segment or file; one record):

| case | fault | error code seen | outcome |
|---|---|---|---|
| 0 | arithmetic (SIGFPE) | 600203 crash ×2 | quarantined, clean |
| 1 | null dereference | 600203 ×2 | quarantined, clean |
| 2 | protected page | 600203 ×2 | quarantined, clean |
| 3 | write to .rodata | 600203 ×2 | quarantined, clean |
| 4 | stack overflow | 600203 ×2 | quarantined, clean |
| 5 | memcpy to NULL | 600203 ×2 | quarantined, clean |
| 6 | index out of bounds | 600203 ×2 | quarantined, clean |
| 7 | null function pointer | 600203 ×2 | quarantined, clean |
| 8 | 1 TB malloc | **720203** custom ×2 | reported, quarantined, clean |
| 9 | double free (SIGABRT) | 600203 ×2 | quarantined, clean |
| 10 | buffer overflow | 600203 ×2 | quarantined, clean |
| 11 | SHM grow (not a fault) | — | completes; the STORAGE_MEM resize path absorbs the doubled batch, nothing leaks |
| 12 | hang | **602203 timeout ×2** | killed by the 30 s alarm on the first run and on the retry, quarantined, clean |

Error codes are `code*1000 + pipeline*100 + module_index`: 600 = crash,
602 = timeout, 700+ = the module's own code (20 here). The `500000` at the
start of every log is the daemon's pipeline-lookup probe at startup, not a
fault. **13 of 13 as intended.** Cases 11 and 12 were re-run after the driver's
expectations were corrected (11 completes by design; 12 needs a run long
enough for two 30 s timeouts — the first pass had ended the run in the
middle of the retry, and the segment it reported leaked was that kill's).

## A9 — the monolith made comparable (2026-09-18)

`single_module.cpp` — the "no decomposition" arm of Fig. 5 — hardcoded
class 7 (Residential; the target is water, 9), the old `NORM_MINMAX`
rendering, a `warpAffine` rotation, no `train_res` resize, a JPEG XL encoder
at a fixed effort and a segmentation gate without the mask fill, and its
cost hints were placeholders (10 µs / 5 µWh) that the scheduler took at face
value. Rewritten so that it does exactly what the five modules do with the
same parameters, in one process: one-pass gain rendering and `cv::rotate`
(as `demosaic_module.cpp`), 224 tiles, `train_res` classification with
`class_index` (as `tflite_classification.cpp`), `pixel_threshold` /
`mask_lowpass` / `threshold_percentage` segmentation (as
`tflite_binary_segmentation.cpp`), and **the same JPEG 2000 encoder** — the
codestream writer was lifted out of `jpeg2000_module.c` into
`utils/j2k_encode.c` and both link it. The 188-byte config slot cannot carry
every key, so the monolith's code defaults *are* the flight values and slots
9 / 10 (14 = 10) name only the two models, the compression ratio and the
layer count. Pipeline 4 = monolith low / high.

**Measured** on `cs12_test_matched_00` (Earth-average mix), n=20, bracketed
floors 2.71 W, same session as nothing else — against the decomposed
static pipelines on the same frame (`analysis/stage4.5-models/e2e_matched_mix.json`):

| effort | monolith | decomposed (pipelines 2 / 3) | overhead of decomposition |
|---|---:|---:|---:|
| low | **1,781 ms · 459 µWh** | 2,140 ms · 519 µWh | +359 ms · +60 µWh (+17 % / +12 %) |
| high | **6,147 ms · 2,708 µWh** | 6,482 ms · 2,744 µWh | +335 ms · +36 µWh (+5 % / +1.3 %) |

A near-constant ~350 ms per frame for the four hand-offs (fork, `.so` load,
SHM copy, checkpoint), a small energy term — the price of 27 operating points
and per-module commitment against the monolith's two and whole-frame
commitment. Files: `monolith/mono_{low,high}{,_cold}.json`; slots 9 / 10 / 14
carry these as hints (`latency_cost` 1,781,000 / 6,147,000 µs; `energy_cost`
459 / 2,708 µWh).

## Persistence policy: durable at entry, exit and halts — STORAGE_HYBRID (2026-09-21)

**The decision.** Persisting every module's output (the paper's MMAP mode)
pays a fixed cost per batch to avoid a loss that only materialises if a
reset lands inside the batch. With reset rate λ and batch time T the
expected loss without checkpoints is ≈ λ·T²/2 per batch; checkpointing costs
C_sync per batch always; it pays iff **λ > 2·C_sync/T²**. Measured: T is
2.1–6.5 s on the Earth-average frame and C_sync ≈ 1.0–1.5 s (MMAP's
per-frame overhead below), so the break-even is one reset every **4–14
seconds** — four orders of magnitude from anything DISCO-2 could see. eMMC
wear points the same way (~45 MB written per batch vs ~10 MB). So the
default is **STORAGE_HYBRID**: the raw frame is persisted at ingest (the only
copy; the producer boundary from Stage 3), modules run on shared memory, the
output is persisted at upload, and the batch's storage is released. The one
place an intermediate checkpoint can pay is a **scheduler halt** — a batch
refused by the energy or latency gate waits indefinitely, so the expected
loss term is unbounded — and that is the one place HYBRID writes one
(user's choice, option B, 2026-09-21).

**Mechanism** (`src/image/image_store.c`, `src/dipp_process.c`). The
durable form of a batch is always a *(file, progress)* pair in a durable
queue entry; the shared-memory segment is a cache of it. Ingest writes the
raw frame (progress −1). When a batch is selected and its segment is gone —
always after a reset — the file is loaded into a fresh segment
(`HYBRID_LOAD`). Modules are handed `STORAGE_MEM` and know nothing of the
file. A halt with new progress writes the segment to a new file, fsyncs,
commits the queue entry naming it, and only then removes the previous file
(`CHECKPOINT`; write → sync → commit → unlink, the Stage 3 ordering); a halt
with no new progress writes nothing. Completion releases segment and file.
A failure keeps the file's progress and drops the segment, so the retry
reloads the file. At startup every entry's segment id is forgotten — the
kernel may reuse an id — and the orphan scan reclaims unreferenced files.
`DIPP_HALT_AFTER_MODULE=k` is a test hook that halts each batch once at
module k exactly as a refused gate does.

**Cost on one frame** (`persistence/e2e_*.json`, `table6.json`;
`cs12_test_matched_00`, n=20, floors bracketed before and after each run,
every bracket within 0.013 W). The board's idle state differs between
sessions, so each difference is taken between runs made in the *same*
state: MEM vs HYBRID in the 3.04 W state, HYBRID vs MMAP in the 2.70 W
state (HYBRID measured in both).

| mode | low | high | difference |
|---|---:|---:|---|
| MEM — nothing durable | 520 µWh · 2.16 s | 2,750 µWh · 6.63 s | — |
| **HYBRID** — entry, exit, halts | **700 µWh · 2.17 s** | **2,945 µWh · 6.63 s** | **+181 / +195 µWh, 0 ms** over MEM |
| MMAP — every module output | 1,072 µWh · 3.07 s | 3,451 µWh · 7.55 s | +314 / +461 µWh, +0.81 / +0.88 s over HYBRID |

HYBRID's price is a constant ~190 µWh per frame — the 10 MB ingest write and
fsync, and the load into shared memory — and no module latency at all: the
modules run exactly as in MEM (demosaic 259 vs 242 ms, tile 105 vs 103).
Per-module persistence adds a further 300–460 µWh and ~0.85 s per frame,
almost all of it on the two full-frame stages (demosaic 531 ms, tile 410 ms
in MMAP). **Table VI is these three rows.**

**Halt path on hardware** (`persistence/halt_log.txt`): five batches through
low with the hook at module 3 — load from ingest at −1, halt, checkpoint at
progress 2, move to the partial queue, resume from the segment, complete,
remove.

**Reset path — real power cuts** (`tools/c3_powercut.py --storage HYBRID`,
`persistence/powercut_hybrid.txt`): 20 batches through the high pipeline
(6.5 s each) with the halt hook at module 3, the first cut 8 s after the feed
and one every ~8 s of daemon time thereafter, 10 cuts (three needed the
60 s recovery cut). **fed 20 · completed 20 · lost 0 · duplicated 0**;
16 batches checkpointed at a halt; boots found `ingest=15 partial=2` →
`9/3` → `2/6` → `0/1` → `0/0` and resumed the partial-queue batches from
their checkpoint files (`HYBRID_LOAD … progress=2`) while ingest batches
restarted from their frames. The energy gate's start-up refusals (the
simulated battery begins on the safety margin after every boot) exercised
the "halt with no new progress" case repeatedly and wrote no redundant
checkpoint.

Three harness lessons on the way, each of which first produced a wrong
number: (1) waiting 90 s on the feed let every batch finish before the first
cut — a null trial that reported 12/12; (2) the daemon's log on the board is
**not durable under a power cut** (ext4 drops its last seconds; 2 boots of 10
survived in one trial), so the evidence is now read over the console before
every cut and tallied host-side; (3) even so, a batch that completed in the
seconds before a cut left the queue durably but its completion line did not
survive — trial 3 reported 6 "lost" that were exactly those — so the daemon
now writes a **durable completion ledger** (`/usr/share/dipp/completed.log`,
one fsync'd line per uploaded batch, before the batch leaves its queue).
That is also what the ground needs to reconcile downlinks after a reset.
