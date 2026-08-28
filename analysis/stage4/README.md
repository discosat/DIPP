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
