# Stage 4 — B4 parser, first end-to-end result

`analysis/parse_runs.py` turns raw run artifacts into two tidy CSVs. Fixtures
here are a real 20-batch run through pipeline 2 (static LOW) on 2026-08-28.

    ./analysis/parse_runs.py <rundir> -o analysis/parsed
    ./analysis/parse_runs.py --trace <trace.json> -o analysis/parsed

## Validated against every fixture available

| Fixture | Result |
|---|---|
| this run (`console.log.gz` + `trace.json.gz`) | **100 executions, 20 batches**, matching the board's own counts |
| `stage2-verification/trace-20batches.json.gz` | 1,456 events, 1 header, clean |
| archived `…-5qt-adaptive.json` | **`duplicate_headers=501`** detected and de-duplicated; 81,367 events |
| archived `…-static-low.json` (139 MB) | **`truncated_at_cap=1000000`** flagged; parsed in 3.2 s |

`unclosed_spans=1` is expected on a cleanly stopped run — the outer
`process_images_loop` BEGIN never gets its END.

## Why the EXEC line is the primary source, not the trace

It carries all 23 fields already, it is line-buffered so it survives a kill, and
`MTR_MAX_ARGS` is 1 so the trace could not carry them anyway. The trace is the
timeline source (Figs 8/9) and a cross-check on counts. The archived traces
additionally carry no `batch_uuid`/`module_index` args at all, so they can
support aggregate distributions but never per-batch tables.

Durations are integer microseconds end to end. The published
`section_durations.csv` used pandas `.microseconds` — the sub-second
*remainder* — and 3 of its 125 rows wrapped, e.g. 1,299,137 µs recorded as
299,137. Integers make that class of error unrepresentable rather than fixed.

## First real numbers

Per-module, n=20, pipeline 2 (static LOW):

| idx | module | median measured | config hint | ratio |
|---:|---|---:|---:|---:|
| 0 | `libdemosaic` | 908,112 µs | 373,000 | **2.44×** |
| 1 | `libtile` | 436,166 µs | 143,000 | **3.05×** |
| 2 | `libtflite_classification` | 260,594 µs | 235,000 | 1.11× |
| 3 | `libtflite_binary_segmentation` | 109,636 µs | 120,000 | 0.91× |
| 4 | `libjpegxl_module` | 168,672 µs | 149,000 | 1.13× |

**The two non-ML stages are 2.4–3× slower than their cost hints; the three
inference stages are within 13%.** The scheduler picks effort by comparing
predicted cost against a deadline budget, so it is being systematically misled
about exactly the stages the decomposition argument rests on. This is C7e's
cost-hint sensitivity, no longer hypothetical, and it wants stating in W7:
per-module cost hints are only as good as their calibration, and these were not
calibrated.

Also first measurements for analyses that previously had **no data source at
all** (added to `EXEC` in `94e810a`):

- **eMMC write volume: ~46 MB per batch** (233,758,720 → 1,152,794,624 bytes
  cumulative across 20 batches). C12's wear arithmetic can now use a measured
  figure instead of an estimate.
- **Child peak RSS: 211,284 kB (206 MB)** against a 42 MB daemon. The envelope
  turns on the forked module processes, not the daemon.
- **SLO slack: +16.4 s to +54.3 s**, no violations in this run — the C8 CDF has
  its input.

95 of 100 executions were cache hits and 5 were misses, one per module, matching
Stage 2.

---

# Stage 4 — B4 tables, first end-to-end table and figure

`analysis/tables.py` turns the parser's CSVs into the two worked artifacts
Stage 4 owes. One command chain, raw run directories to LaTeX:

    ./tools/campaign.py experiments/matrices/smoke.yaml
    ./analysis/parse_runs.py runs/smoke/* -o analysis/parsed
    ./analysis/tables.py --parsed analysis/parsed --out analysis/stage4

Outputs here: `table6_overhead.{tex,md,json}` and `fig_timeline.{pdf,png}`
(SoC, both queue depths, and chosen effort against wall-clock time — the
Figs 8/9 replacement).

## The time axis is real, not reconstructed

`EXEC` now carries `ts_us`, the `CLOCK_MONOTONIC` value already computed for
`slack_us`. Cumulative measured latency is not a substitute: it omits the idle
gaps between arrivals, which under a Poisson process are most of the timeline.

## Config slot N is daemon `param_id` N-1

The flight config numbers slots from one, matching the CSP params
`module_param_1..12`; the daemon's `param_id` is a zero-based index into
`module_parameter_lists`. Verified against latency hints — slots 1/2/3/6/11
(373000, 143000, 235000, 120000, 149000 us) are exactly what the daemon
reported at indices 0/1/2/5/10. Off by one here silently mislabels every
effort level, which is why `tests/test_parse_runs.py` pins it.

## First MEM vs MMAP numbers — preliminary, and they do not look small

Two runs, 5 batches each, pipeline 2 (static LOW), first batch discarded:

| Stage | MEM median | MMAP median | Overhead |
|---|---:|---:|---:|
| `libdemosaic` | 365.4 ms | 912.3 ms | **+149.7%** |
| `libtile` | 154.5 ms | 429.6 ms | **+178.0%** |
| `libtflite_classification` (low) | 245.9 ms | 259.1 ms | +5.4% |
| `libtflite_binary_segmentation` (low) | 91.2 ms | 109.8 ms | +20.4% |
| `libjpegxl_module` (low) | 152.8 ms | 170.5 ms | +11.6% |

**This is one launch per cell, not the five the table requires, so the tool
stamps the caption "Preliminary" and warns on stderr.** Treat it as a signal
about where to look, not a result: persistence cost tracks how much data a
stage moves, so it lands almost entirely on the two non-ML stages that carry
full-resolution frames, and barely touches inference. If C4 reproduces the
shape at n>=5, the paper's single 0.24% overhead figure is not merely too
small, it is the wrong shape -- one number where the truth is per-stage and
spans two orders of magnitude.

## Reporting rules the tool enforces so nobody has to remember them

- The first batch of every run is discarded — it measures first-touch, not
  steady state.
- Dispersion is median + IQR over *independent process launches*; a cell built
  from one launch is marked preliminary rather than quietly reported.
- The MMAP-minus-MEM difference carries a 95% bootstrap CI, and a cell whose CI
  spans zero prints `n.s.` instead of a percentage. "Indistinguishable from
  zero" is stronger and less attackable than a spuriously precise 0.24%.

## Three harness defects fixed here, all the same shape

Each produced a clean-looking null result — the failure mode this revision
exists to eliminate.

1. **The completion wait matched its own echo.** `campaign.py` polled
   `test -f board.json && echo OK` and tested for `"OK"`, but the console
   echoes the command before its output. The wait returned at the first poll,
   the driver gzipped `console.log` out from under a still-draining `run.sh`,
   and `run.sh` — which counts completions by grepping that file — read 0 four
   times, declared the run stable and wrote `n_executions=0, build_id=""`. The
   board had executed everything correctly. The readiness probe
   (`echo BOARD_READY`) had the same shape and the same latent failure: a board
   stranded at the *U-Boot* prompt echoes typed characters too, so it would
   report a healthy Linux on a board that has none. Both markers are now
   assembled at runtime, so the literal exists only in the reply.
2. **A CR in the build banner made `board.json` unparseable.** `build_id` came
   from `grep`ping the daemon's banner, which ends with `\r`; an unescaped
   control character inside a JSON string invalidates the whole record, so two
   fully successful runs were reported failed. Stripped with `tr`.
3. **`campaign.py` was block-buffered.** Hours unattended with no visible
   progress and no partial record if killed.

Also added: **power-cycle recovery.** The board hangs mid-U-Boot roughly one
boot in twenty, and it did. Without recovery, one wedge at run 12 of 100 fails
every run after it as well — each still paying the full reboot timeout — so a
single fault that a five-second Otii cut clears costs an entire overnight
campaign.
