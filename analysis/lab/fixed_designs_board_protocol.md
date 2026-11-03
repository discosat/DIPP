# Fixed designs on the board — protocol, fixed 2026-11-02, before any run

## Question

The argument for adaptiveness: an operator without SIPP fixes one effort
combination for every content mix, built either for average content or for the
worst case, and that design fails somewhere. The two designs (chosen from the
cost model, not from U: the highest-quality combination whose frame time fits the
6.68 s frame period on Earth-average / clear-sky content) were so far only
simulated (oracle_soft_summary.md):

- avg-design   high/med/high (cls/seg/enc)
- worst-design med/med/low

The simulator is known to be pessimistic for overloaded static policies (up to
0.36, board-grid/SUMMARY.md), which is where these designs collapse. Their
collapse cells and their best cells against SIPP are therefore run on the board.

## Runs (experiments/matrices/gen_board_fixed.py → board-fixed.yaml, run set board-fixed)

Seed 1, scenario identical to the board grid's run of the same cell (trace,
content, phase, start charge, platform, scheduler defaults; checked: the only key
that differs from the grid's final-policy run is `pipeline`). Build s5a-final7 =
s5a-final6 (the build of the grid's re-checks; same code as the E37 runs)
byte-identical except storage.vmem, whose slots 4 and 5 hold the two designs
(experiments/board/flight-config/manifest_fixed_designs.yaml; the daemon has six
pipeline slots, and 4/5 — monolith, demosaic+jxl — are unused by the grid).
cost.seed is identical to s5a-final6's. After the runs s5a-final6 is redeployed.

| run | why | simulated U (seed 1) | simulated V_G1 | board SIPP U (grid, seed 1) |
|---|---|---|---|---|
| P1C3-avg | avg collapse | 0.160 | 0.239 | 0.901 |
| P1C4-avg | avg collapse | 0.094 | 0.157 | 0.884 |
| P2C5-avg | avg collapse | 0.187 | 0.310 | 0.799 |
| E37P2C3-avg | avg collapse (energy deficit) | 0.671 | 0.780 | 0.903 |
| P2C4-avg | avg's largest lead over SIPP | 0.966 | 0.986 | 0.934 |
| P1C4-worst | worst collapse | 0.429 | 0.581 | 0.884 |
| P2C5-worst | worst collapse | 0.412 | 0.698 | 0.799 |
| P3C5-worst | worst's largest lead over SIPP | 0.918 | 0.918 | 0.890 |

## What is reported and the decision rule

Per run: U, V_G (all variants), urgent and routine hit rates, dropped frames,
board vs simulated U. Board SIPP values are the grid's seed-1 runs.

- A collapse cell is **confirmed** on the board if SIPP − design ≥ 0.10 in U.
  If any collapse cell is not confirmed, the paper reports the board value and
  states that the fixed design does not collapse there; the claim "each fixed
  design fails somewhere" is kept only if at least one collapse cell per design
  is confirmed.
- In the two lead cells (P2C4-avg, P3C5-worst) the board difference is reported
  as measured, with the usual tie line 0.02.
- In the paper, board values replace simulated values for these eight
  (cell, design) pairs; the rest of the fixed-design comparison stays simulated
  and is labelled so. Expectation written before the run: the collapses are
  milder on the board than simulated (as for static medium/high in the grid), but
  remain at least 0.10 below SIPP.

## Results (2026-11-03, after all eight runs)

Campaign: runs/board-fixed (8/8 complete; runs/board-fixed-chain.log). The chain
was interrupted once, mid P2C4-avg (its run directory was empty, no result), when
its controlling session ended; it was resumed with the four completed runs skipped
and P2C4-avg re-run from scratch. Two runs needed the campaign's power-cycle
recovery before booting (P1C4-avg, P2C4-avg, both on the first attempt). Parsed
with analysis/parse_runs.py + campaign_utility.py into analysis/board-fixed/.

| run | board U | simulated U | board − sim | SIPP board U | SIPP − design | V_G1 | late / dropped | urgent hit | verdict |
|---|---|---|---|---|---|---|---|---|---|
| P1C3-avg | 0.173 | 0.160 | +0.013 | 0.901 | +0.728 | 0.212 | 183 / 0 | 0.05 | collapse confirmed |
| P1C4-avg | 0.167 | 0.094 | +0.073 | 0.884 | +0.717 | 0.214 | 359 / 0 | 0.07 | collapse confirmed |
| P2C5-avg | 0.206 | 0.187 | +0.019 | 0.799 | +0.593 | 0.325 | 303 / 39 | 0.32 | collapse confirmed |
| E37P2C3-avg | 0.684 | 0.671 | +0.013 | 0.903 | +0.219 | 0.780 | 68 / 0 | 0.62 | collapse confirmed |
| P2C4-avg | 0.987 | 0.966 | +0.021 | 0.934 | −0.053 | 0.987 | 0 / 0 | 1.00 | design ahead (beyond tie) |
| P1C4-worst | 0.712 | 0.429 | +0.283 | 0.884 | +0.172 | 0.860 | 97 / 0 | 0.59 | collapse confirmed (milder) |
| P2C5-worst | 0.461 | 0.412 | +0.049 | 0.799 | +0.338 | 0.760 | 215 / 0 | 0.70 | collapse confirmed |
| P3C5-worst | 0.918 | 0.918 | +0.000 | 0.890 | −0.028 | 0.918 | 0 / 0 | 1.00 | design ahead (beyond tie) |

Decision rule: every collapse cell is confirmed (SIPP − design ≥ 0.10), so
"each fixed design fails somewhere" stands on board data for both designs. SIPP
met every urgent deadline in all six collapse cells; the designs met 5–70 %.
In their best cells the designs beat SIPP by 0.053 (avg, P2C4) and 0.028 (worst,
P3C5); simulation had 0.062 and 0.028.

Simulator: as expected, the board is never worse than simulated for these fixed
designs (+0.000 to +0.283). It is within 0.05 in six of eight runs; the
exceptions are the two runs in P1C4 (20 s / 30 s, 2× Earth average): +0.073
(avg) and +0.283 (worst), the same cell and direction as static medium in the
grid (+0.154). The paper uses the board values for these eight pairs; the
worst-design collapse in P1C4 is reported as 0.71 (board), not 0.43.
