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
