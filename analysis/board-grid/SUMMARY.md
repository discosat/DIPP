# Board grid: results (2026-10-19; all 83 runs, blocks A-D)

Source: analysis/board-grid/all/{runs.csv, compare.md} (parse_runs ->
campaign_utility -> board_grid_compare, predictions
analysis/board_grid_predictions_fixed.csv). U = utility within deadline,
V_G1 = soft-deadline utility (METRIC.md 2026-10-15), Wh = Otii whole-board
energy. Seed 1 unless stated. P2 statics and deadline-only are the eval_final
runs (same traces, content, deadlines; unchanged code path).

## Final policy against the best static policy

| cell | low | med | high | deadline-only | final | best static | regret | final urgent hit |
|---|---|---|---|---|---|---|---|---|
| P1C2 | .791 | .949 | .887 | .999 | .948 | med | .001 (tie) | 1.00 |
| P1C3 | .791 | .474 | .054 | .201 | .901 | low | **-.110** | 1.00 |
| P1C4 | .791 | .448 | .049 | .734 | .884 | low | **-.093** | 1.00 |
| P1C5 | .121 | .042 | .014 | .035 | .117 | low | .003 (tie) | .05 |
| P2C1 | .791 | .949 | 1.000 | 1.000 | .999 | high | .001 (tie) | 1.00 |
| P2C2 | .791 | .949 | 1.000 | 1.000 | .999 | high | .001 (tie) | 1.00 |
| P2C3 | .791 | .949 | .590 | .814 | .963 | med | -.015 (tie) | 1.00 |
| P2C4 | .791 | .949 | .403 | .962 | .934 | med | .015 (tie) | 1.00 |
| P2C5 | .791 | .336 | .134 | .166 | .799 | low | -.008 (tie) | 1.00 |
| P3C2 | .791 | .949 | 1.000 | 1.000 | .999 | high | .001 (tie) | 1.00 |
| P3C3 | .791 | .949 | 1.000 | 1.000 | .999 | high | .001 (tie) | 1.00 |
| P3C4 | .791 | .949 | 1.000 | 1.000 | .970 | high | **.030** | 1.00 |
| P3C5 | .791 | .938 | .812 | .812 | .890 | med | **.048** | 1.00 |

Energy deficit (block D: dawn-dusk, 3.7 W for the payload, board always on,
start 0.05 Wh above the floor; build s5a-final5):

| cell | low | med | high | deadline-only | final | best static | regret | final urgent hit |
|---|---|---|---|---|---|---|---|---|
| E37P2C2 | .791 | .949 | .518 | .986 | .974 | med | **-.025** | 1.00 |
| E37P2C3 | .791 | .949 | .387 | .445 | .903 | med | **.046** | 1.00 |
| E37P4C2 | .791 | .949 | 1.000 | 1.000 | .986 | high | .014 (tie) | 1.00 |
| E37P4C3 | .791 | .949 | .987 | .987 | .987 | high | -.001 (tie) | 1.00 |

Seeds (final policy, 3 each): P2 cells range <= .011; P1C3 .901; P1C5
.116-.118; P3C3 .999; P3C5 .889-.890. Seed-to-seed spread never exceeds .011.

## Findings

1. The best static policy changes with the deadline pair and the cell:
   high (P2/P3 at nominal load), medium (P2C3-C4, P3C5), low (P1C3-C5, P2C5).
   No single static choice is right across the grid.
2. Tight deadlines (P1, 20/30 s): the final policy beats the best static
   policy by .09-.11 in C3 and C4 with every urgent deadline met; medium,
   high and deadline-only miss most urgent frames there (.05-.33).
3. P2 (20/300 s): ties everywhere (|regret| <= .015).
4. P3 (60 s / 30 min): first regrets above the tie line, .030 (C4, vs
   high) and .048 (C5, vs medium), as predicted before the runs (.043 /
   .036). The cost buys safety: in P3C5 high and deadline-only drop 80
   frames at the queue cap and meet 70 % of urgent deadlines, medium drops
   5 (urgent .95); the final policy drops none (urgent 1.00).
5. C2 at P1 is the safety margin's cost: deadline-only .999 vs final .948
   (final stays at medium whenever a frame waits: worst-case high 12.15 s
   does not fit twice in the 15 s budget).
6. P1C5 is beyond the platform: even static low reaches .121.
7. Soft deadlines (V_G1): the final policy is never late, so V = U; static
   high in P1C2 overtakes it (.968 vs .948); in P1C3/C4 deadline-only rises
   (.763/.853) but stays below the final policy (.901/.884).
8. Energy (Otii): in P3C4 the final policy trails high by .030 but uses 30 %
   less energy (2.67 vs 3.79 Wh); in P3C5 it trails medium by .048 AND uses
   8 % more (3.63 vs 3.36 Wh; high 4.97). At P1C3/C4 it uses ~8 % more than
   static low (1.63 vs 1.51, 1.68 vs 1.55 Wh) for +.09-.11 U.

9. Energy deficit at P2 (300 s): static high collapses (C2 .518, C3 .387;
   urgent .43/.38) and deadline-only collapses on clear sky (C3 .445,
   urgent .57); the final policy meets every urgent deadline and beats the
   best static in C2 (.974 vs .949) but trails medium in C3 (.903 vs .949,
   regret .046). It is the only policy that waits to recharge (459-2,670
   energy waits per run) rather than running the battery to the floor.
10. Energy deficit at P4 (6 h): everything that is not static low/medium
    reaches .99-1.00; the final policy ties (.986, .987) and is the only one
    with urgent 1.00 in C3 (high and deadline-only .95: 3 frames unfinished
    at the end). It uses less energy than high/deadline-only in every
    deficit cell (e.g. P4C3 3.31 vs 3.88 Wh).

## Board vs simulator

- 78 of 83 runs within .03 of their prediction; the final policy within
  .034 in every cell, nominal and energy deficit.
- P3 predictions were blind (written before the grid, unchanged by the
  2026-10-16 prediction fix): all 20 runs within .025.
- The simulator is pessimistic for overloaded static high / medium /
  deadline-only (up to .36 at P1C2 high), as known.
- Re-runs of block A (for the Otii traces) reproduce the originals within
  .005 (runs/board-grid-notrace/).

## Block D history (not results)

Earlier block D attempts are in runs/board-grid-feederbug/ (harness feeder,
reboot and daemon halt defects, 2026-10-17) and runs/board-grid-floatbug/
(float battery level, 2026-10-18; every policy far below the simulator at
P2). The results above are the re-run on s5a-final5 (METRIC.md addenda
2026-10-17 and 2026-10-18).

## Build provenance (binary hash is not recorded per run; by deploy time)

| runs | daemon build | feeder |
|---|---|---|
| block A originals (now in board-grid-notrace), block B except P3C3-med | s5a-final2 | original |
| P3C3-med-s1 | s5a-final2 | first fix (never resumed in a gap: same behaviour) |
| 7 block A trace re-runs, block C | s5a-final4 | final fix |
| block D | s5a-final5 | final fix |

No nominal run took a changed path (no energy halts near the floor, no
untouched parks, no fast-forward resumes), and the re-runs reproduce their
s5a-final2 originals within .005.
