# Evaluation metric and scheduling defaults — fixed 2026-10-07, before any evaluation run

This file is written before the first adaptive evaluation run of the
resubmission campaign and is not edited after it. A change after that date
is a new, dated section below, never a rewrite.

## Utility within deadline, U

For a run with N frames (dropped frames count in N):

    U = (1/N) · Σ over frames that completed by their deadline of q(frame)

    q(frame) = mean over {classification, segmentation, encoder} of
               quality(stage, effort used) / quality(stage, high)

A frame that misses its deadline, is dropped, or never completes contributes
0. Per-stage quality, measured on CloudSEN12 / EuroSAT-MS test data
(analysis/stage4.5-models/README.md):

| stage | measure | low | medium | high | q low / medium / high |
|---|---|---:|---:|---:|---|
| classification | EuroSAT-MS test top-1 | 75.13 % | 92.09 % | 98.52 % | .763 / .935 / 1 |
| segmentation | CloudSEN12 test cloud IoU | .607 | .775 | .807 | .752 / .960 / 1 |
| encoder (J2K L1/L2/L3) | PSNR, one 512 B frame lost, 3 kB/tile | 32.51 dB | 36.04 dB | 37.88 dB | .858 / .951 / 1 |

Always reported next to U (U alone hides how it was reached): deadline hit
rate per class (urgent / routine), mean q of on-time frames, late and dropped
counts, latency p50 / p95 / p99 / max per class, water-and-clear tiles
delivered on time and downlink bytes (R1.4), per-orbit depth of discharge.

Robustness of the ranking to the weighting: U is recomputed with q taken from
one stage only (classification-only, encoder-only); a ranking that flips under
either is reported as such.

Tie: |ΔU| < 0.02 between two policies in a cell is a tie.

## Regret

In each evaluated cell c (one load × one content set):
B(c) = max over static low, medium, high of U(c) (best static policy in
hindsight); R_p(c) = B(c) − U_p(c) (negative = p beats every static policy);
worst-case regret = max over the evaluated cells of R_p(c). The cell set is
listed in the campaign matrix before it runs.

## Scheduling defaults (src/include/sched_policy.h)

Thresholds on the total queue depth D (both queues), decided before each
batch: D < Q1 best effort over all levels; Q1 ≤ D < Q2 best effort capped at
medium; D ≥ Q2 lowest effort. One rule sets both:

    Q_e = floor(SLO_routine / (k · s_e_max))

s_e_max = the per-frame service time at effort e on the worst-case content
(the clear-sky keep-all frame, every tile kept), from the fitted cost model
(analysis/cost_model.json) plus 0.3 s batch overhead: s_high_max = 12.15 s,
s_medium_max = 7.33 s. With SLO_routine = 300 s and **k = 2**:
**Q1 = 12, Q2 = 20**. Read: the queue is too deep for effort e once draining
it at e on worst-case content would take more than half the routine SLO.

Sensitivity is reported over k (k = 1.5 … 4) — host simulator
(tools/sched_sim.py) for the curve, two or three bench points to validate it.
Pre-registration evidence (host simulator, 15 cells, 2026-10-06): k ∈ [1.5, 4]
gives worst-case regret 0.05–0.08; the reference content must be worst-case
(average content breaks the rule).

Gates (unchanged from the shipped policy): medium is considered only when the
per-module budget is below 5 s, low below 3 s (best-effort tier); the medium
tier considers medium then low without gates.

Granularity: **stage** (yield at a module boundary iff a waiting frame has a
strictly earlier deadline and the batch can be parked); batch granularity is
the ablation.

## SLOs

Routine 300 s; urgent 20 s. The urgent value is the worst-case urgent latency
the scheduler can guarantee (non-preemptive blocking by one batch plus the
frame's own path, 17.9 s at the slowest operating point on Earth-average
content) rounded up; 15 s was infeasible by construction. Under stage
granularity blocking is one module, not one batch.

## Platform

DISCO-II BOL (experiments/platforms/disco2-bol.env), board powered per
burst, battery floor = 2 orbits of bus load without generation (41.6 Wh),
start charge = the platform-only steady state at the trace's start phase.

## Addendum 2026-10-09 — after the evaluation (not a rewrite of the above)

Two changes, made after the 52 evaluation runs and because of them. Both are
reported in the paper as such.

1. **Bench fault: the trace feeder compressed arrivals.** At a gap between
   acquisition stretches, `feed_trace.sh` waited for the daemon to be idle
   before fast-forwarding, without bound. When a backlog outlasted the gap
   (every overloaded run), the feed fell behind and every overdue frame was
   then fed at once (C5-adaptive-s1: 351 s wait at a 138 s gap, about 60
   frames 0.09 s apart). 21 of 52 runs have a frame fed > 1 s late. Fixed:
   the wait ends when the next frame is due (`tests/test_feed_trace.sh`
   case 2b); `campaign.py` now fails any run whose feeder reports a late
   frame. The affected runs are redone (eval2.yaml); the runs with no late
   frame stand.
2. **Policy change: a utilisation guard.** With the feeder fixed, the
   simulator (recalibrated to the board's measured per-frame overhead:
   0.30 s per batch + 0.41 s per ingested frame) still puts the default
   policy 0.12 below static low on 2x clear sky (C5), where even the lowest
   effort cannot keep up (rho_low = 1.66). The guard forces the lowest tier
   whenever rho_low = (lowest-effort service per frame + per-frame overhead)
   / (median frame cadence) >= 1, from the daemon's own measurements
   (sched_policy.h). Threshold 1 is the queueing stability bound, not a
   tuned value; window 8 frames (simulator: 4-16 identical); overhead 780 ms
   (measured). Simulator: C5 0.662 -> 0.784 (= static low), C1-C4 unchanged,
   worst-case regret 0.121 -> 0.051. All runs of the default policy and its
   ablations are redone with the guard; `adaptive-noguard` in C5 (3 seeds)
   is the ablation that shows its effect. The metric, Q rule, k, SLOs and
   cells above are unchanged.

## Addendum 2026-10-11 — default policy v2 (replaces the 2026-10-09 utilisation guard)

The utilisation guard of the 2026-10-09 addendum was never run on the bench.
An overnight simulation study (tools/policy_lab.py; 2,000 selection
scenarios, 3,000 held-out, a 900-cell regime grid, the real traces, model
error, adversarial search; paper-rev/policy_v2_spec.md) rejected it (held-out
p99 regret 0.131 against 0.085 for the policy evaluated on 2026-10-07) and
selected policy v2, now the default:

* frames carry their capture time; the policy uses per-frame deadline windows
  (d - a) and has no notion of frame classes;
* budget B = (largest deadline window among the last 8 frames) / k, k = 2;
  depth thresholds Q_e = floor(B / s_e_max) (12 / 20 at a 300 s window, as
  before);
* overload guard: lowest effort while even the lowest effort falls behind
  (rho_low >= 1) and the lowest-effort backlog would reach B before the
  current overload is expected to end (the longest overload seen so far);
* per-module acceptance: an upgrade only if the frame's planned path stays
  within (d - a) / k of its capture and every queued frame stays feasible at
  lowest effort; the 5 s / 3 s gates are gone.

The metric, the cells, the SLO values given to frames and the tie rule are
unchanged. Held-out (quoted once): p99 / max regret 0.071 / 0.184 against
0.085 / 0.243 for v1; worst policy-caused urgent hit 0.82 against 0.33.
eval2.yaml re-runs every default-policy run and ablation with v2, the
feeder-affected runs, a guard ablation (C5 x 3) and v1 with the fixed feeder
(C3, C5); analysis/eval_merge.py assembles the final set.

## Addendum 2026-10-14: board grid for the final policy (predictions before any run)

Policy: the final policy (paper-rev/policy_final.md; daemon build g055264e+).
Grid: experiments/matrices/board-grid.yaml (gen_board_grid.py), 107 runs:
deadline pairs P1 (20 s, 30 s), P2 (20 s, 300 s), P3 (60 s, 1800 s),
P4 (300 s, 21600 s); static low/med/high, deadline-only, final; blocks A-D
(A: final at P2 x C1-C5 x 3 seeds; B: P1/P3/P4 x C2-C5 x 5 policies; C: final
seeds 2-3 at P1/P3/P4 x C3/C5; D: energy deficit dawndusk-3w7 x P2/P4 x
C2/C3 x 5 policies). Metric unchanged (U, regret vs best static, tie 0.02).
Simulated predictions for every run, fixed before the first board run:
analysis/board_grid_predictions.csv (analysis/predict_board_grid.py). The
simulator is known to be up to 0.08 pessimistic for static high and
deadline-only on 1x clear sky; no tuning to the board results.

## Addendum 2026-10-15: secondary metric, soft-deadline utility V_G

Added before any board-grid result beyond block A (P2, final policy only,
whose statics are the eval_final runs) was analysed. U stays the primary
metric; V_G is a secondary robustness check, reported for every policy and
cell, never used to select anything.

A frame delivered late still has some value. Per frame f:
  V_G = (1/N) sum_f q_f * g(late_f / D_f),  g(x) = max(0, 1 - x / G)
late_f = max(0, completion - deadline); D_f = the frame's own relative
deadline (its class SLO); q_f as in U. Frames never delivered (dropped at the
ingest cap, unfinished) count 0. G = 0 is U exactly; reported G = 0.5, 1, 2
(a frame keeps value until G of its own deadlines late; G = 1: worth 0 at
twice the deadline). Variant "urgent hard": urgent frames keep g = 0 when
late (an alert is worthless late), routine frames soft. Regret and the tie
rule (0.02) apply to V_G unchanged. Expected direction (stated before
computing): V_G favours policies that deliver many frames late (static high,
deadline-only in overloaded cells), so the final policy's lead in C3-C5
shrinks; any change of ranking is reported.

## Addendum 2026-10-16: prediction bug (P1/P3/P4), corrected file alongside

analysis/board_grid_predictions.csv (committed e458c2d before the grid) is
wrong for every run on a pair trace (P1, P3, P4): the pair traces already
carry the pair's routine deadline per frame, and predict_board_grid.py scaled
it again by routine/300 (P1 frames got 3 s instead of 30 s, P3 10800 s, P4
1555200 s). P2 runs (trace deadline 300 s, factor 1) are unaffected. Found
when P1 board results disagreed with every prediction (16/20 > 0.03).
Mechanical fix only (frames unscaled, policy routine parameter = the pair's
value, assertion that frame deadlines equal the trace's); no model change.
Corrected predictions: analysis/board_grid_predictions_fixed.csv, written
2026-10-16 after P1 and P3C2 results existed (so these are not blind for P1;
they are blind for P3C3 onward and P4). All 83 unchanged rows are identical
(every P2 row among them). The original file is kept as committed.

## Addendum 2026-10-16: nominal P4 dropped from the board grid

Decided after P1 and P3C2 results, from the (blind, unchanged) P3/P4
predictions: with routine deadlines of 30 min and 6 h the final policy's
budget is set by the queue capacity, not the deadline, so P4 predicts the
same as P3 in every cell. The 24 nominal P4 runs (block B P4 x C2-C5 x 5
policies, block C P4 seeds 2-3) are removed; P4 stays in block D (energy
deficit), where recovering needs the longer deadline. Grid: 83 runs. No run
already made is removed and no kept run's spec changed. Same day: the
extra-seed block C moved after block D (run order only).

## Addendum 2026-10-17: feeder fix, block D (energy deficit) re-run

Harness defect, found in block D (dawndusk-3w7): the first frame after an
idle gap was fed 2-12 s late in 7 of 9 runs (campaign: "feeder late", run
failed). Cause: tests/board/feed_trace.sh fast-forwards a gap in chunks
while the daemon is halted; the advance recharges the battery, the daemon
resumes mid-gap, and later chunks wait for its running module (advances
are applied between modules), so the frame after the gap is late by up to a
module time and part of the gap is fast-forwarded while the payload works.
Nominal runs never halt at a gap and are unaffected (0 late feeds in blocks
A and B). Fix: a chunk is sent only while the daemon is idle or halted;
once it resumes, the rest of the gap is real time (FF lines log resumed=1).
Host test 2c added (fails on the old feeder, passes on the new). Every block
D run made with the old feeder (E37*, 9 runs) is moved to
runs/board-grid-feederbug/ and block D is re-run in full with the fixed
feeder; no other run changes.
Correction, same day: the first fix stopped fast-forwarding for the rest of
a gap once the daemon resumed; in the deficit regime it resumes and halts
repeatedly, so long gaps ran in real time and E37P2C2-med exceeded its time
budget (moved to runs/board-grid-feederbug/E37P2C2-med-s1.fix1-timeout).
Final fix: while the daemon works the feeder waits (bounded by the next
frame's time) until it is quiescent again, then goes on advancing; never an
advance while it works. Test 2c covers it (fails on both earlier versions).
E37P2C2-low and P3C3-med, run with the first fix, never resumed in a gap
(resumed=0 on every FF line), where the two fixes behave identically, and
are kept.
Second harness defect, found the same day while checking the feeder fix:
the per-run reboot silently did not happen. The ttyUSB numbers had swapped
(console on ttyUSB1), the console driver's port probe then took 10.4 s,
and the campaign's 15 s budget for `sync; reboot` killed the driver before
`reboot` was typed; the campaign only checked that Linux answered. SysV
segments left by unfinished batches of energy-deficit runs then accumulated
(47 segments / 464 MB from E37P2C2-greedy on, 141 / 1.4 GB later) and the
OOM killer killed module children (E37P2C2-med-s1 with the fixed feeder:
41 SIGKILLs). Every nominal run (blocks A, B) started with 0 leftover
segments and ~10 MB shared memory (pre.txt), so none is affected. Fixes
(campaign.py): reboot verified by the board's /proc/uptime (else reboot
again, then power cycle, else the run fails); 60 s for the reboot command;
console timeouts while waiting for the board count as "not up yet";
runs/board-grid-chain2.sh pins BOARD_TTY to the console's /dev/serial/by-id
name. The OOM-affected run is in runs/board-grid-feederbug/
(E37P2C2-med-s1.fix2-oom); block D continues with both fixes.
Third defect, same day, in the daemon (not the harness): an energy-halted
batch from the INGEST queue that made no progress was moved to the partial
queue (only partial-queue heads entered the quiet halted state), and the move
counted as work, so the main loop never slept and moved the next ingest head
every pass. Under a long deficit the partial queue filled with untouched
batches; at MAX_QUEUE_SIZE the daemon spun on one batch and the feeder died
(E37P2C2-high-s1, 26 of 222 frames). Fix (src/dipp_process.c run_one_batch):
a batch halted with nothing done stays in its own queue and returns
"halted"; halt state is tracked per queue; the main loop sleeps when either
head is halted. Nominal runs never took this path (0 untouched parks in
every block A/B run and in eval_final C5), so their behaviour is unchanged.
Build s5a-final3 (dipp + feed_trace.sh changed vs s5a-final2), deployed
27/27. Block D is re-run in full on it; every earlier block D run is in
runs/board-grid-feederbug/.
Follow-up to the daemon fix (same day): with HYBRID storage an ingest entry
names only its file, so a batch now kept halted in ingest was re-loaded into
a new segment on every retry and the old one leaked (E37P2C2-med-s1 on
s5a-final3: 69 loads of one batch, then the camera could not hand frames
over). Fix: a segment created on that pass is committed to the halted
entry once (HALT_KEEP_SEGMENT); retries reuse it. Build s5a-final4 (dipp
only vs s5a-final3), deployed 27/27; block D restarted on it. deploy.py
now requires the push's last line to be exactly MATCH.

## Addendum 2026-10-18: battery level was a float; block D re-run again

Found while analysing block D (board far below the simulator for every
policy at E37P2, e.g. static low C3 0.652 vs 0.791, static med C2 0.731 vs
0.949, final 0.640 vs 0.961; per-frame energy and time identical in both).
src/battery_simulator.c kept the stored energy only in the battery_level
parameter, a float. At ~41.6 Wh one float step is 3.8 uWh; the real-time
thread steps every ~10 ms, and the deficit platform's 0.4 W surplus adds
1.1 uWh per step -- below half a float step, so every gain rounded away and
the battery recharged only across fast-forward steps (1 s). Measured on the
board: 0.405 W missing over the first 150 s of E37P2C3-low. Fix: the level is
integrated in double, the parameter is a float mirror (host test added:
fails on the old code, passes on the new; 53/53). Build s5a-final5 (dipp
only vs s5a-final4). Block D (20 runs) is re-run on it by
runs/board-grid-chain3.sh after the current chain; the float-battery runs go
to runs/board-grid-floatbug/.
Scope: the nominal DISCO-2 runs (blocks A-C, eval_final) integrate the same
rounding (small sunlit surpluses are lost; eclipse drains are quantised by
up to ~4 %), but no nominal run was energy-limited: every SCHED_HALT in
them was well above the floor (0 of 7 within 0.05 Wh) and their U matches
the simulator. Their modelled battery TRACES (timeline figures, per-orbit
depth of discharge) are biased and must not be quoted without a re-check;
the Otii measurements are real hardware power and are unaffected.
