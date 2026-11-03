# All board results (seed 1), utility within deadline U, with the dynamic oracle

Board: analysis/board-grid/compare_all.md (board grid, 83 runs; P2 statics and deadline-only from runs/eval_final) and analysis/board-fixed/runs.csv (fixed designs avg = high/med/high, worst = med/med/low; 8 runs). – = not run on the board.
Oracle columns are SIMULATION, seed 1 (analysis/lab/oracle_results.csv, oracle_lp.csv): UB = min(integer bound, LP bound), an upper bound under the simulator's cost model; best of 27 = best single (cls/seg/enc) combination for all frames, in hindsight (a reachable schedule, hence a lower bound on the true optimum, which lies between it and UB); SIPP sim = SIPP simulated on the same scenario. SIPP board / UB divides the board value by the simulated bound (the simulator is within 0.034 of SIPP on the board).

| cell | low | med | high | avg-design | worst-design | deadline-only | **SIPP** | best fixed | SIPP − best fixed | SIPP urgent hit | oracle UB (sim) | best of 27 (sim) | SIPP sim | SIPP board / UB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P1C2 | 0.791 | 0.949 | 0.887 | – | – | 0.999 | **0.948** | med 0.949 | -0.001 | 1.00 | 0.997 | 0.987 (high/med/high) | 0.949 | 95 % |
| P1C3 | 0.791 | 0.474 | 0.054 | 0.173 | – | 0.201 | **0.901** | low 0.791 | +0.110 | 1.00 | 0.937 | 0.918 (med/med/low) | 0.891 | 96 % |
| P1C4 | 0.791 | 0.448 | 0.049 | 0.167 | 0.712 | 0.734 | **0.884** | low 0.791 | +0.093 | 1.00 | 0.896 | 0.848 (med/low/low) | 0.850 | 99 % |
| P1C5 | 0.121 | 0.042 | 0.014 | – | – | 0.035 | **0.117** | low 0.121 | -0.004 | 0.05 | 0.566 | 0.110 (low/low/low) | 0.107 | 21 % |
| P2C1 | 0.791 | 0.949 | 1.000 | – | – | 1.000 | **0.999** | high 1.000 | -0.001 | 1.00 | 1.000 | 1.000 (high/high/high) | 1.000 | 100 % |
| P2C2 | 0.791 | 0.949 | 1.000 | – | – | 1.000 | **0.999** | high 1.000 | -0.001 | 1.00 | 1.000 | 1.000 (high/high/high) | 0.999 | 100 % |
| P2C3 | 0.791 | 0.949 | 0.590 | – | – | 0.814 | **0.963** | med 0.949 | +0.014 | 1.00 | 0.994 | 0.970 (high/med/med) | 0.959 | 97 % |
| P2C4 | 0.791 | 0.949 | 0.403 | 0.987 | – | 0.962 | **0.934** | avg-design 0.987 | -0.053 | 1.00 | 0.988 | 0.970 (high/med/med) | 0.905 | 95 % |
| P2C5 | 0.791 | 0.336 | 0.134 | 0.206 | 0.461 | 0.166 | **0.799** | low 0.791 | +0.008 | 1.00 | 0.846 | 0.791 (low/low/low) | 0.797 | 94 % |
| P3C2 | 0.791 | 0.949 | 1.000 | – | – | 1.000 | **0.999** | high 1.000 | -0.001 | 1.00 | 1.000 | 1.000 (high/high/high) | 1.000 | 100 % |
| P3C3 | 0.791 | 0.949 | 1.000 | – | – | 1.000 | **0.999** | high 1.000 | -0.001 | 1.00 | 1.000 | 1.000 (high/high/high) | 1.000 | 100 % |
| P3C4 | 0.791 | 0.949 | 1.000 | – | – | 1.000 | **0.970** | high 1.000 | -0.030 | 1.00 | 1.000 | 0.988 (high/high/high) | 0.945 | 97 % |
| P3C5 | 0.791 | 0.938 | 0.812 | – | 0.918 | 0.812 | **0.890** | med 0.938 | -0.048 | 1.00 | 0.997 | 0.928 (high/med/low) | 0.888 | 89 % |
| E37P2C2 | 0.791 | 0.949 | 0.518 | – | – | 0.986 | **0.974** | med 0.949 | +0.025 | 1.00 | 0.993 | 0.987 (high/med/high) | 0.961 | 98 % |
| E37P2C3 | 0.791 | 0.949 | 0.387 | 0.684 | – | 0.445 | **0.903** | med 0.949 | -0.046 | 1.00 | 0.977 | 0.952 (med/med/high) | 0.904 | 92 % |
| E37P4C2 | 0.791 | 0.949 | 1.000 | – | – | 1.000 | **0.986** | high 1.000 | -0.014 | 1.00 | 1.000 | 1.000 (high/high/high) | 0.985 | 99 % |
| E37P4C3 | 0.791 | 0.949 | 0.987 | – | – | 0.987 | **0.987** | high 0.987 | +0.000 | 1.00 | 1.000 | 0.996 (high/high/high) | 0.987 | 99 % |
