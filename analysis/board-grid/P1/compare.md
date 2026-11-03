# Board grid: comparison (seed 1 unless stated)

U = utility within deadline; pred = simulated before the run; regret = best static U - final U (tie if |regret| < 0.02); Wh = Otii whole-board energy over the run.

| cell | low | med | high | greedy | final | best static | final regret | final seeds U | final pred | final urgent hit (min) | final Wh vs best static Wh |
|---|---|---|---|---|---|---|---|---|---|---|
| P1C2 | 0.791 | 0.949 | 0.887 | 0.999 | 0.948 | med | 0.0006 (tie) | 0.948-0.948 (n=1) | 0.9487 | 1.00 | 1.51 vs 1.51 |
| P1C3 | 0.791 | 0.474 | 0.054 | 0.201 | 0.901 | low | -0.1101 | 0.901-0.901 (n=1) | 0.8911 | 1.00 | 1.63 vs 1.51 |
| P1C4 | 0.791 | 0.448 | 0.049 | 0.734 | 0.884 | low | -0.093 | 0.884-0.884 (n=1) | 0.8501 | 1.00 | 1.68 vs 1.55 |
| P1C5 | 0.121 | 0.042 | 0.014 | 0.035 | 0.117 | low | 0.0034 (tie) | 0.117-0.117 (n=1) | 0.1069 | 0.05 | 2.32 vs 2.33 |

Worst final regret (seed 1): 0.0034 in P1C5

## Board vs prediction (every grid run)

| run | U board | U pred | diff |
|---|---|---|---|
| P1C2-final-s1 | 0.9481 | 0.9487 | -0.0006 |
| P1C2-greedy-s1 | 0.9990 | 0.9953 | +0.0037 |
| P1C2-high-s1 | 0.8874 | 0.5270 | +0.3604 |
| P1C2-low-s1 | 0.7910 | 0.7910 | +0.0000 |
| P1C2-med-s1 | 0.9487 | 0.9487 | +0.0000 |
| P1C3-final-s1 | 0.9011 | 0.8911 | +0.0100 |
| P1C3-greedy-s1 | 0.2009 | 0.1742 | +0.0267 |
| P1C3-high-s1 | 0.0541 | 0.0495 | +0.0046 |
| P1C3-low-s1 | 0.7910 | 0.7910 | +0.0000 |
| P1C3-med-s1 | 0.4743 | 0.4145 | +0.0598 |
| P1C4-final-s1 | 0.8840 | 0.8501 | +0.0339 |
| P1C4-greedy-s1 | 0.7339 | 0.7235 | +0.0104 |
| P1C4-high-s1 | 0.0486 | 0.0417 | +0.0069 |
| P1C4-low-s1 | 0.7910 | 0.7910 | +0.0000 |
| P1C4-med-s1 | 0.4480 | 0.2943 | +0.1537 |
| P1C5-final-s1 | 0.1174 | 0.1069 | +0.0105 |
| P1C5-greedy-s1 | 0.0345 | 0.0277 | +0.0068 |
| P1C5-high-s1 | 0.0139 | 0.0139 | +0.0000 |
| P1C5-low-s1 | 0.1208 | 0.1099 | +0.0109 |
| P1C5-med-s1 | 0.0417 | 0.0395 | +0.0022 |

Largest |board - pred|: 0.3604 (P1C2-high-s1); runs within 0.03: 16/20
