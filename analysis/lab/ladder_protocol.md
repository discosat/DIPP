# SIPP over a ladder of per-stage effort combinations — protocol, fixed 2026-11-01, before any run

Motivation (oracle_summary.md): the best single (cls/seg/enc) combination in hindsight
beats SIPP by 0.02–0.07 in about half the board-grid cells; the winners use
segmentation at medium (high/med/high, high/med/med, med/med/low). SIPP's menu is the
three uniform efforts, so between medium (7.3 s worst case) and high (12.4 s) it has
nothing.

## Policy (tools/policy_lab.py, `RungLadder`, policy set `ladder`)

SIPP's final rule, unchanged except that "effort x" becomes a rung r:
- rungs = Pareto frontier of the 27 combinations in (worst-case frame time, q), from the
  cost model (99 tiles at every stage) and the per-stage quality values (QUAL),
  ordered by q: lll, mll, lml, mml, hml, mmm, hmm, hmh, hhh (5.0 … 12.4 s);
- guard (falling behind for good) → rung 0;
- fits(r): queue depth d < ⌊B / s_r^max⌋, i.e. (d + 1)·s_r^max ≤ B;
- before each module m: the highest rung r ≤ the highest fitting rung whose effort for
  m is feasible (the unchanged `feasible()`: m at that effort, the frame's later
  modules and every other frame at lowest, energy-timed walk, path within 1/k);
- energy check / wait rule unchanged. No new constants (k = 2 as before).

The per-stage quality values become an input of the policy (the operator's declared
value of each stage). They are used only to order the rungs, not tuned on any trace.

## Regression

`RungLadder(rungs="uniform")` (rungs lll, mmm, hhh) must reproduce `sipp` exactly
(U, hit rates) in every (cell, seed); otherwise the implementation is wrong.

## Runs and report

The 17 board-grid cells, seeds 1–3, scenario `build_scenario` as for the oracle.
Policies: static low/med/high, sipp, sipp-rungs-uniform, sipp-ladder. Reported per cell
(means over seeds): U of sipp and sipp-ladder, their difference, urgent hit rate, and
both against the oracle's UB, achievable, best27 and best uniform (oracle_results.csv,
oracle_lp.csv). Ties: |ΔU| < 0.02.

Expectation written before the run: sipp-ladder gains where SIPP holds back at medium
(P1C2, P2C3, P2C4, E37P2C2), small gains in P1C3/P1C4, little change in P3C4/P3C5
(there the loss is the safety margin, not the menu); it should not lose more than
0.02 anywhere.

## Addendum 2026-11-01 (after the first ladder run) — sweep of the safety factor k

Result of the run above: 17 ties. Diagnosis: fits(r) costs every rung at worst-case
(clear-sky) content, so the intermediate rungs rarely fit (P1C2: high/med/high needs
2 × 9.7 s ≤ B = 15 s with one frame waiting). The menu only matters if the policy is
less conservative. Sweep, in simulation only:

- k ∈ {1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0}, with path_k = k (as in the existing
  sipp-k1.5 / sipp-k3 ablations); all else as the frozen policy;
- for both sipp (uniform efforts) and sipp-ladder (Pareto rungs);
- 17 board-grid cells × seeds 1–3.

Reported per (policy, k): mean U over cells; wins/ties/losses and worst difference vs
the frozen sipp (k = 2); mean and worst gap to best27 and to the best uniform static;
lowest urgent hit rate over cells; total dropped frames. The robustness cost of a lower
k is read from the worst cell and the urgent hit rate, not from the mean.

Expectation written before the sweep: lower k raises U where content is lighter than
worst case (P1C2, P2C3, P2C4, E37P2C2) and the ladder starts to separate from uniform
efforts there; lower k costs most in the clear-sky and 2x cells (P1C3–C5, P2C5, P3C5)
and in the energy-deficit cells.

## Addendum 2026-11-01 — stress test of lower k (jitter, cost-model error)

The k sweep above used the nominal cost model with no jitter, where a smaller safety
margin cannot be penalised. Repeat it under the stressors the margin exists for
(levels as in analysis/lab_deadline.py):

- nominal: no jitter, exact model;
- jitter 0.15: per-execution latency × lognormal(σ = 0.15);
- model error 0.3: the policy's cost model (all its time and energy estimates,
  including s_r^max) = true × exp(N(0, 0.3)) per module configuration, drawn per run;
- both: jitter 0.15 + model error 0.3;
- optimistic model: the policy believes every module costs 0.8 × the truth
  (true costs 25 % above belief).

Policies: frozen sipp and sipp-ladder, each at k ∈ {1.25, 1.5, 1.75, 2.0} (path_k = k);
17 cells × seeds 1–3; error draws seeded per (cell, seed). Reported per stress level:
for each (policy, k) mean U, W/T/L and worst Δ vs frozen sipp at k = 2 under the same
stress, lowest urgent hit rate over cells excluding P1C5 (infeasible for every policy),
total urgent misses, dropped frames.

Decision rule written before the run: a lower k (with or without the ladder) is
defensible only if, at every stress level, it has no loss beyond the 0.02 tie line in
any cell against frozen k = 2 and no lower urgent hit rate (P1C5 excluded).

Implementation note (before any stress result): with a wrong cost model the believed
Pareto frontier can lack all-low or all-high (a believed-cheaper medium dominates low);
rung 0 is kept all-low (the guard's fallback, as in the frozen policy) and the top rung
all-high. Nominal ladder results are byte-identical after this change.
