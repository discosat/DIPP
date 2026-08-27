# Stage 2 verification, on hardware

20 batches through pipeline 2 (static low), 5 modules each, on the board
2026-08-27. Binaries deployed over the serial console; every file md5-verified
at both ends.

`trace-20batches.json.gz` is the trace from the second run.

## Results

| Criterion | Result |
|---|---|
| Battery charged per execution | **100 debits for 100 executions** (20 batches x 5 modules) |
| Cache misses in the same run | **5** — what the old code would have debited |
| Trace is valid JSON | parses; 1,456 events; ends `]}` |
| Exactly one header | **1** |
| Dropped events | **0** |
| Trace self-describing | metadata event carries the full `BUILD_ID` |

**A1.** 100 debits against 5 cache misses is a **20x** correction. It also
explains the audit's "19 of 500" exactly: the old code debited once per *unique*
(module, config, input-size) key, so with 5 such keys it charged 5 times no
matter how many batches ran. The debited values match the flight config
per-module costs (first 718 uWh = demosaic, last 264 uWh = tile/jxl-low), and
the run total of 38,356 uWh matches 20 x 1,931 uWh/batch.

**A5, trace noise.** The first run produced a **76,391,996-byte** trace for this
workload. After suppressing tracing of empty queue polls and of the
`get_queue_size` accessor, the identical workload produced **218,457 bytes** --
a **350x** reduction with the same 100 executions recorded. That is the
"99.29% of events" figure from the audit, confirmed: 99.71% of the trace was
idle-poll noise.

**A5, other items.** `headers=1` proves the fork-flush fix works on real
hardware with real forks. The daemon was stopped with SIGTERM and still wrote a
complete, terminated trace. The idle log spam is gone: 41 lines in the first
seconds where the old build produced 600-900 per second.

## Note on the event counts

B=502 against E=501 is expected: the outermost `process_images_loop` BEGIN has
no matching END because the daemon was terminated inside that loop.

`get_lowest_effort_implementation_config` fired 150 times and
`get_best_effort_implementation_config` 50 -- the heuristic switched as the
queue depth crossed `LOW_QUEUE_DEPTH_THRESHOLD`, so both paths were exercised.
