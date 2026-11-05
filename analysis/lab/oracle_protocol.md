# Dynamic oracle — protocol, fixed 2026-10-30, before any oracle result

This file is written before the first oracle solve and is not edited after it.
A change after that is a new, dated section below, never a rewrite.

## Question

How far is SIPP from the best possible effort assignment on the board-grid
workload? Best-static-in-hindsight only covers the three uniform efforts
(low/med/high for all stages). The oracle chooses, for every frame, the effort of
each of the three stages (27 combinations) jointly over all frames, knowing all
arrivals, content and energy in advance.

Environment: OR-Tools 9.12.4544 in an isolated venv,
`python3 -m venv --system-site-packages ~/.venvs/dipp-oracle &&
~/.venvs/dipp-oracle/bin/pip install ortools==9.12.4544` (it brings numpy 2.x
into the venv only; the system interpreter keeps conda's numpy 1.26.4). The
simulator regression is checked under the venv interpreter as well.

## Model (analysis/oracle.py, OR-Tools CP-SAT)

Scenario: exactly the simulator scenario used for the board-grid predictions
(`build_scenario` in analysis/predict_board_grid.py): the cell's trace, content
set, urgent/routine deadlines, fixed costs (over 0.30 s, ingest 0.41 s,
ckpt 0.5 s), and for E37 cells the dd3.7 energy regime. Durations and energies
are the simulator's cost model (`policy_lab.Cost`, nominal, no jitter), as
integers (ms, µWh).

Per frame f (arrival a_f, deadline d_f, tiles n_seg, n_enc):
- one fixed interval, start ≥ a_f, length ingest_s + over + demosaic + tile;
- one interval per stage (cls, seg, enc) whose length is the duration of the
  chosen effort (exactly one of three Booleans y_{f,s,e}); order fixed
  → cls → seg → enc;
- NoOverlap over all intervals (one processor; any order; frames may interleave
  at module boundaries);
- on-time Boolean o_f ⇒ end(enc_f) ≤ d_f;
- objective max Σ_f Σ_s z_{f,s,e}·QUAL[s][e]·1000/3 with z ≤ y, z ≤ o, which is
  1000·U·N (QUAL from tools/sched_sim.py; q is separable on the board content).

Energy:
- Nominal cells: none — the simulator's nominal predictions have no battery
  (energy never binds at DISCO-2 power; no nominal board run came near the floor).
- E37 cells: reservoir over the energy above the floor. Initial level w0 − floor.
  The board is always on: the net idle rate p_sun − bus − p_idle (+0.40 W) is
  credited as increments at the start of each 1 s step (optimistic). Each
  module's energy is debited at its end (as the simulator debits on
  completion), rounded down (optimistic). Level ≥ 0 at all times; no cap
  (clipping only loses energy, so ignoring it is optimistic).

Relaxations with respect to the simulator (each can only raise the optimum, so
the solver's bound is an upper bound under the simulator's cost model): no
100-frame queue cap; no checkpoint cost on parking; ingest may be deferred
and merged with the frame's fixed part; any processing order (the simulator
uses EDF); late frames can be postponed to the end at no cost.

Solve: time limit 600 s per (cell, seed), 7 workers. Report the best solution
objective, CP-SAT's best bound (= UB), status, and gap.

## Replay

The oracle's per-frame effort assignment is replayed through `simulate()` by a
`FixedAssignment` policy (EDF order, all simulator semantics). U_replay is
achievable, so U_replay ≤ true optimum ≤ UB. Both are reported.

## Static references

All 27 combinations, each fixed for all frames, simulated with `simulate()`;
best27_static_U is the maximum, best_uniform_static_U the maximum over the three
uniform ones.

## Cells, seeds, what is reported

The 17 board-grid cells (P1 C2–C5, P2 C1–C5, P3 C2–C5, E37 P2/P4 × C2/C3),
seeds 1–3. Per (cell, seed): UB, U_oracle (solver solution), U_replay, status,
gap, best27_static_U and combo, best_uniform_static_U, U_sipp (simulated
`sipp`, policy set final3), SIPP as % of UB and of U_replay; per frame the chosen
combination.

Sanity (must hold, else a modelling error is reported, not hidden):
UB ≥ U_replay; UB ≥ best27_static_U; UB ≥ U_sipp (up to the relaxations, which
only add freedom); the all-high and all-low fixed models reproduce simulated
static high/low within 0.03 on P2C2; a 5-frame toy instance matches brute force.

## Addendum 2026-11-01 — solver formulation changed before the full run

The CP-SAT scheduling model above (intervals + NoOverlap + reservoir) gives no
useful bound at full size: on P1C3 seed 1 after 60 s its bound was the trivial
U = 1.0 and its best solution (0.881) was below SIPP's 0.891. It is replaced by
the **preemptive relaxation** of the same problem, solved exactly:

- per frame, choose one effort per stage or skip the frame (x, p Booleans);
  maximise the quality of on-time frames (same objective);
- each frame fits its own window: fixed part + stage durations ≤ d_f − a_f;
- Horn's condition: for every window [t1, D] (t1 a release, D a deadline), the
  work of on-time frames with a_f ≥ t1 and d_f ≤ D is ≤ D − t1. Windows with
  D − t1 ≤ 120 s are added up front (one per distinct set of contained frames);
  longer ones lazily: solve, run preemptive EDF on the chosen set, add the
  violated windows, repeat until EDF meets every deadline (then the optimum of the
  relaxation is reached) or the time budget ends (then CP-SAT's best bound of the
  last solve is still an upper bound);
- E37: cumulative energy of on-time frames with deadline ≤ D (module energies,
  rounded down) ≤ energy above the floor at time D (start charge + net idle
  generation +0.40 W × D, rounded up), for every deadline D;
- per-iteration relative gap 1e-3; time budget 900 s per (cell, seed);
  reported UB = best bound.

Preemption at any instant, skipping, free order and the energy condition only at
deadlines are all relaxations of the simulator, so UB remains an upper bound under
the simulator's cost model. The replay, static references and sanity checks are
unchanged; the checks (fixed all-high / all-low on P2C2; 3-frame toys on P1C5 and
E37P2C3 with 8 s deadlines, vs brute force over 27³ assignments) pass, and on the
P1C5 toy the new and the old formulation give the same optimum (0.6221).

Development runs (solver convergence only, nothing tuned to SIPP's result):
seed 1 of P1C3, P2C5, P3C5, E37P2C3.

## Addendum 2026-11-01 (after the integer run) — LP bound for cells the integer model does not converge on

The integer window model did not converge within 900 s on P1C4 (seeds 1, 3: no
solution; seed 2: bound 0.975) and P1C5 (bound ≈ 0.66) — the 2x-load cells with
20 s / 30 s deadlines (432 frames). Their integer bounds are valid but loose. Added:
the LP relaxation of the same window model (GLOP; x, p ∈ [0, 1]; Horn's windows
added lazily from preemptive EDF on the fractional work; same E37 energy rows).
The LP optimum bounds the integer optimum, and with only a subset of the windows
it bounds the full LP from above, so it is an upper bound in every case. On the
converged cells tested it equals the integer bound to 4 decimals (P1C3 0.9373,
P2C5 0.8456, E37P2C3 0.9769). Reported UB per (cell, seed) = min(integer best
bound, LP bound). The integer run's results are kept unchanged.

## Addendum 2026-11-01 — gap decomposition and soft-deadline bound

1. `analysis/oracle_gap.py`: exact frame-by-frame split of U_oracle − U_sipp into
   under-effort (both on time, SIPP lower quality; per stage), lateness (oracle on time,
   SIPP late or unfinished) and triage (SIPP on time, oracle not), for every (cell, seed)
   with an OPTIMAL integer oracle solution.
2. Soft deadlines (METRIC.md addendum 2026-10-15, V_G with g = max(0, 1 − late/(G·D))):
   `oracle.py soft`, the LP bound with each frame completing at its deadline or at one
   of 8 lateness levels d + l·G·D/8 (l = 1..8), each valued at g((l − 1)/8) (the
   least-late point of its interval: optimistic, so still an upper bound); urgent
   frames only at the deadline in the urgent-hard variant; same windows (lazily from
   preemptive EDF over the (frame, level) pieces) and E37 energy rows. Variants:
   G = 0.5, 1, 2 (both classes soft) and G = 1 urgent hard. Compared with SIPP's and
   the best fixed combination's V_G from simulation (SIPP's policy unchanged: it does
   not know about soft deadlines). Time limit 1800 s per solve.
   Expectation written before the run: where SIPP has no late frames (every cell but
   P1C4, P1C5, P2C5) V_SIPP = U_SIPP while the bound rises, so the gap widens; in the
   overload cells SIPP's late frames earn partial value, so its gap may close there.

Run note (soft bound, 2026-11-01): 203 of 204 solves completed; P2C5 seed 2 G = 0.5
failed with GLOP status 4 (numerical), which aborted the pool before the CSV was
written. The 203 results were recovered verbatim from the run log; the failed solve
was re-run alone with a retry on numerical failure (dual simplex, then presolve
off) and converged. oracle_soft.csv = the 203 recovered rows + the re-run row.

Bug found in the soft run (2026-11-01): for solves stopped by the time limit, the
returned objective was read after new window rows had been added, which resets
GLOP's solution (value 0): the 24 unconverged P1C4/P1C5 solves and P2C5 seed 1 G = 2
reported UB = 0 (P2C5 G = 2 cell mean 0.600 < SIPP) — impossible for an upper bound,
caught by the UB ≥ SIPP check. Fixed: the bound returned is the objective of the last completed
solve (an LP over a subset of the windows: still an upper bound). Converged solves
(and every hard-deadline LP, all converged) are unaffected. The 25 unconverged
solves are re-run alone with the fix; their rows replace the wrong ones.

Re-run (2026-11-02): all 27 solves (P1C4, P1C5 × 4 variants × 3 seeds; P2C5 G = 2 × 3
seeds, re-run as a group) completed; the background command's non-zero exit came from
a trailing `tail`, not from a solve. P2C5 G = 2: seeds 2 and 3 reproduce their earlier
converged values exactly (0.9000, 0.9014); seed 1 converged this time (0.9007, 813 s).
P1C4/P1C5: none converged within 1800 s; their bounds (P1C4 0.91–0.92, P1C5 0.70–0.78)
are valid but loose (above the hard LP bound by 0.02 and 0.14–0.21, and not monotone in
G). Every row now satisfies UB_soft ≥ V_SIPP and UB_soft ≥ hard UB. The replaced rows
are kept in the session scratchpad (oracle_soft_before_rerun.csv); oracle_soft.csv and
oracle_soft_summary.md are regenerated.

## Addendum 2026-11-03 — the replay is not reported as "achievable"

The protocol reported U_replay (the oracle's per-frame combinations replayed through
simulate()) as an achievable reference. It is a poor one: the combinations are
optimised for the relaxed problem (preemption, free order, skipping, no cap), and
under the simulator's rules the replay loses 0.01–0.18 to the relaxed objective
(means over seeds: P3C5 0.997 → 0.820, P2C5 0.843 → 0.754, P1C4 0.625 against
best27 0.848), falling below SIPP in P1C4, P2C5 and P3C5. It beats the best single
combination in 7 of 47 (cell, seed) runs, by at most ≈0.02. Reported from now on:
UB (upper bound) and best27 (best single combination in hindsight, reachable, so a
lower bound on the true optimum under the simulator's rules, which lies between the
two). U_replay stays in oracle_results.csv; oracle_summary.md no longer has the
"achievable" column. No result changes.

## Addendum 2026-11-05 — bound with the board's minimum per-frame overhead

The bound was computed under the simulator's fixed per-frame time (ingest 0.41 s + over
0.30 s = 0.71 s), which exceeds what the board spends between back-to-back frames
(median 0.37 s, mean 0.40 s, minimum 0.13 s; P1C2-high, P1C2-greedy, P1C4-med; module
latencies themselves match the model to within about 5 ms). It was therefore valid only
under the simulator's cost model, and one board result lay above it: P1C2 deadline-only
0.999 against UB 0.9972.

`oracle.py lp --fixed-s 0.13 --tag fixed013` (all 51 solves converged) re-solves the LP
with the observed minimum: lab/oracle_lp_fixed013.csv. No board value exceeds this bound.
The earlier oracle_lp.csv / oracle_results.csv stay as they are. SIPP on the board reaches
89.1–99.9 % of this bound (seed 1) in every cell except P1C5 (18.6 %, infeasible for every
policy). The bound is deliberately generous: 0.13 s is the smallest gap seen, not typical.
