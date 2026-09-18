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
