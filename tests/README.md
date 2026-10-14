# Tests

## `test_trace_fork.c`

Regression test for the defect that made every archived trace invalid JSON.

```sh
gcc -o /tmp/test_trace_fork tests/test_trace_fork.c src/utils/minitrace.c \
    -Isrc/include -Isrc/include/utils -DMTR_ENABLED=1 -pthread
/tmp/test_trace_fork
```

Builds natively; minitrace is portable, so this needs no board and no SDK.

**Verified to catch the defect.** Rebuilt with the two flushes removed (the
`fflush(f)` after the header in `mtr_init_from_stream`, and the `fflush(NULL)`
before `fork()`), the same test reports:

```
FAIL: 201 headers, expected exactly 1
```

201 headers for 200 forked children — the same N+1 pattern the audit found in
the archived traces (~501 headers for ~500 module executions). With the fixes
in place it reports exactly 1 header and 0 dropped events.

Note the corrupt trace is *larger* than the correct one (3,607 B vs 209 B):
the duplicated headers were most of the file.

## `test_shadow_paging.c`

```sh
gcc -o /tmp/test_shadow tests/test_shadow_paging.c \
    src/priority_queue/priority_queue_mmap.c src/priority_queue/priority_queue.c \
    src/priority_queue/priority_queue_mem.c \
    -Isrc/include -Isrc/include/priority_queue -Isrc/include/image \
    -Isrc/include/utils -Isrc/include/protobuf -Isrc/include/cost_store -pthread
/tmp/test_shadow
```

Writes the states a power cut can leave behind directly into the queue file,
which is deterministic where a real cut is a race. Six cases: clean recovery,
torn inactive slot (ignored), **torn active slot (must roll back to the other
slot)**, torn header (detected, not trusted), and that the file stays usable
afterwards.

It immediately caught a defect that would have made the whole mechanism
useless: **`msync()` requires a page-aligned address.** With the header at 512 B
and slots at 20,808 B, every slot `msync` failed with `EINVAL` and nothing ever
reached storage — silently.

**Correction (2026-08-28).** An earlier version of this note claimed the board's
page size exceeds 4096 and that this is why a 4096-granularity build failed with
`EINVAL` on hardware. That is wrong: the board measures **4 kB**
(`/proc/self/smaps` `KernelPageSize`). The run that produced 64 `QUEUE_SYNC_FAIL`
was launched before the header was widened and was still running the
**512-byte-header** build, whose slot[0] at offset 512 is unaligned on any page
size. The failure was real; the attribution was not.

The layout keeps 64 KiB granularity anyway — headroom against a kernel built
with 16K or 64K pages — and `init` logs `QUEUE_ALIGN_WARN` if the runtime page
size ever fails to divide it.

**Always confirm on the board** after changing anything about the on-disk
layout: check `QUEUE_SYNC_FAIL` is 0 in the daemon log. A host-side pass proves
the logic, not the alignment.

## `test_cost_model.c`

Cost store v2 and the lookup the scheduler decides on.

```sh
gcc -o /tmp/test_cost_model tests/test_cost_model.c \
    src/cost_store/cost_model.c src/cost_store/cost_store.c \
    src/cost_store/cost_store_mem.c src/cost_store/cost_store_mmap.c \
    -Isrc/include -Isrc/include/cost_store -Isrc/include/image
/tmp/test_cost_model
```

Covers the lookup order (measured point -> fitted model -> YAML hint; a
zero hint is rejected at config load in `dipp_config.c`, so nothing below it), the clamp that stops an extrapolated line going negative
and resurfacing as 4.29e9 through `%u`, the EMA (alpha 0.3) that keeps the
first post-boot execution's VX graph compile from owning an input size,
`DIPP_LATENCY_CORRECTION=0` freezing the store, the seed-file round trip in
both backends (a format-1 file must reset, not be believed), and that LRU
eviction spends on-board points before seeded ones.

Energy is asserted to be *never* written from an on-board measurement: a point
created on-board carries the prediction that was debited for it. The board
measures latency; energy comes from the offline sweep.

`cost_model.c` deliberately does not include `heuristics.h`, so the store
and the model build on the host without the generated protobuf headers,
which exist only in the SDK sysroot.

## `test_battery_simulator.c`

```sh
gcc -DDIPP_HOST_TEST -o /tmp/test_batt tests/test_battery_simulator.c \
    src/battery_simulator.c -Isrc/include -Isrc/include/param -lm -pthread
/tmp/test_batt        # from the repository root
```

`-DDIPP_HOST_TEST` swaps libparam's `battery_level` for a plain float, so the
simulator builds with no CSP, no param and no board.

**The simulator has no defaults**, so half of this file is about refusing:
nothing set (every missing input named at once), each required input dropped
in turn, out-of-range and non-numeric values, an unreadable profile, and six
kinds of malformed profile including a line the 128-byte reader would split.
Each must fail configuration; none may fall back to a value. Use of the
process-wide simulator before `battery_simulator_init` aborts. Verified to
have teeth: reintroducing a silent `SOC0` default, or a flat-profile fallback
for a bad file, fails the corresponding cases.

The load-bearing physics case is **`advance(3000)` against a host-side
integration of the same 3,000 s written out separately in the test**: trace
replay compresses the gaps between imaging bursts by fast-forwarding the
battery, which is only legitimate if a jump charges it exactly as living
through the gap would. Also: ten 60 s chunks land where one 600 s jump does;
one orbit nets delivered generation minus bus load (+5.35 Wh for DISCO-II);
time runs 1:1; the battery clamps at empty and full (the physical limits, no
chosen floor) and logs `BATTERY_EMPTY` once; profile interpolation with wrap;
`phase0`; the processing board per burst (nothing before the first activity,
exactly the 60 s hold after an ingest, held on through an execution longer
than the hold, `always`, `off`); the margin derived from one orbit's surplus plus
`RESERVE_FRAC` (and flagged when a platform has no surplus) or given
explicitly; and the digitised DISCO-II profiles delivering 16.435 W (BOL) and
15.257 W (EOL).

**Found a defect on the way:** `battery_simulator_step` reported
`current_state` and `p_gen_now_W` for the *start* of the step it had just
integrated while `log_state` printed the phase it had reached, so a
`BATTERY_FF` line ending just after sunrise printed a sunlit phase beside
`state=ECLIPSE`. The reported state is now sampled at the new time; the
integration still uses the start-of-step power.

## `test_feed_trace.sh`

```sh
sh tests/test_feed_trace.sh        # from the repository root, ~40 s
```

Host test for `tests/board/feed_trace.sh`, the trace-replay feeder `run.sh
--load trace` calls, against a stub daemon: `dipp_ctl` appends the
`BATTERY_FF`/`MARK` lines the daemon would log, the camera records its
arguments, and `SCHED_STATE` lines are written into a fake console. Checks
that frames land within 0.5 s of their simulated times; a compressed gap is
fast-forwarded only once the daemon is idle or halted, never past the next
frame, and less the wall time already spent in it; every advance is confirmed
by a `BATTERY_FF` line; the tail fast-forwards while halted and stops once the
daemon is idle; each frame's SLO reaches the camera; seven kinds of
malformed trace plus a missing argument are refused before anything is fed;
and `--image-list` gives frame k line k mod N, logs it, and refuses a list
naming a missing file or combined with `--image`.

It caught nothing on the host that the board did not also show, but the board
showed one thing the first version missed: the quiescence wait reused the
loop's `t` and logged a wall-clock value as the trace time of every frame after
a gap. The check `feed.log records each frame's own trace time` pins it.

On the board (2026-09-30): a 6-frame trace with one 3,000 s compressed gap
landed every frame within 0.6 s of its simulated time, the second burst halted
in eclipse below the margin, the tail recharged it and all 6 completed;
`BATTERY_FF` summed to the feeder's 5,188.1 s; all 31 eclipse chunks cost
0.21780-0.21790 Wh against 13.07 W x 60 s = 0.21783 Wh. An urgent frame (SLO
15 s) fed behind five routine ones (SLO 300 s) on static high completed
second.

## `test_make_arrival_trace.py`

```
python3 tests/test_make_arrival_trace.py      # ~10 s
```

`tools/make_arrival_trace.py`. Orbit: the sun-synchronous inclination for
DISCO-II's 95.43 min period is 97-98 deg; the profile's dark arc ends at phase
0 (eclipse exit) and lasts 35-36.5 min. Steady state: full at eclipse entry,
and at eclipse exit full minus the bus load over the eclipse. Footprint:
46.73 x 56 km, half-widths checked at 23/24 km and 27.5/29 km; a polygon target
under the frame, 1 deg away, and larger than the frame. CLI: a Poisson trace
has the asked count and urgent fraction, urgent rows carry the urgent SLO,
gaps over the real-time limit are fast-forwarded in full and none under it,
the compressed clock never decreases, the header carries phase0 and
soc0_steady, `feed_trace.sh --check` accepts it, the same seed gives the same
trace, a missing SLO is refused, and an urgent SLO below the EDF bound of a
stub cost model is refused. On the East Greenland windows every frame's phase
is within 30 s of the phase its latitude implies.

## `test_parse_stage5.py`

```
python3 tests/test_parse_stage5.py
```

The Stage 5a tables of `analysis/parse_runs.py` on a synthetic console in the
daemon's exact formats plus a feed.log: frame classes joined through `MARK`,
non-preemptive blocking (an urgent frame arriving 2 s into a routine 4 s
stage is blocked 2 s), latency and deadline met, wait before the first stage,
halts and halted time per batch, energy debited per batch, the dropped batch,
battery and event rows, and the run summary (met fractions, unfinished, battery
minimum over `BATTERY` and `BATTERY_FF`, configuration, `BATTERY_EMPTY` with
its prose tail, the feeder's `TRACE_END`).

## `test_sched_policy.c`

```
gcc -DDIPP_HOST_TEST -Isrc/include -o /tmp/test_sched_policy \
    tests/test_sched_policy.c src/heuristics/sched_policy.c && /tmp/test_sched_policy
```

The default scheduling policy's pure decisions and parameters
(`src/heuristics/sched_policy.c`): the effort tier from the queue depth
(boundaries belong to the lower tier; q_med = q_low reproduces the original
two-tier policy; q = inf never leaves high, i.e. deadline-greedy); the
stage-boundary yield rule (only for a strictly earlier deadline, never when
the batch cannot be parked); the compiled defaults (the rule's 12 / 20, gates
5 s / 3 s, stage granularity); environment overrides including `inf`; and
refusal of non-numeric, negative, unknown-granularity and q_med > q_low
settings.
