# Board grid: comparison (seed 1 unless stated)

U = utility within deadline; pred = simulated before the run; regret = best static U - final U (tie if |regret| < 0.02); Wh = Otii whole-board energy over the run.

| cell | low | med | high | greedy | final | best static | final regret | final seeds U | final pred | final urgent hit (min) | final Wh vs best static Wh |
|---|---|---|---|---|---|---|---|---|---|---|
| P2C1 | 0.791 | 0.949 | 1.000 | 1.000 | 0.999 | high | 0.0007 (tie) | 0.999-0.999 (n=3) | 1.0 | 1.00 | 1.59 vs 1.60 |
| P2C2 | 0.791 | 0.949 | 1.000 | 1.000 | 0.999 | high | 0.0012 (tie) | 0.999-0.999 (n=3) | 0.9993 | 1.00 | 2.04 vs 2.03 |
| P2C3 | 0.791 | 0.949 | 0.590 | 0.814 | 0.958 | med | -0.0097 (tie) | 0.958-0.962 (n=3) | 0.9592 | 1.00 | 2.26 vs 1.82 |
| P2C4 | 0.791 | 0.949 | 0.403 | 0.962 | 0.934 | med | 0.0149 (tie) | 0.933-0.934 (n=3) | 0.9049 | 1.00 | 2.29 vs 2.00 |
| P2C5 | 0.791 | 0.336 | 0.134 | 0.166 | 0.799 | low | -0.0076 (tie) | 0.788-0.799 (n=3) | 0.7966 | 1.00 | 2.47 vs 2.58 |

Worst final regret (seed 1): 0.0149 in P2C4

## Board vs prediction (every grid run)

| run | U board | U pred | diff |
|---|---|---|---|
| P2C1-final-s1 | 0.9993 | 1.0000 | -0.0007 |
| P2C1-final-s2 | 0.9993 | 1.0000 | -0.0007 |
| P2C1-final-s3 | 0.9993 | 1.0000 | -0.0007 |
| P2C2-final-s1 | 0.9988 | 0.9993 | -0.0005 |
| P2C2-final-s2 | 0.9993 | 0.9996 | -0.0003 |
| P2C2-final-s3 | 0.9991 | 0.9985 | +0.0006 |
| P2C3-final-s1 | 0.9584 | 0.9592 | -0.0008 |
| P2C3-final-s2 | 0.9621 | 0.9592 | +0.0029 |
| P2C3-final-s3 | 0.9623 | 0.9593 | +0.0030 |
| P2C4-final-s1 | 0.9338 | 0.9049 | +0.0289 |
| P2C4-final-s2 | 0.9341 | 0.9039 | +0.0302 |
| P2C4-final-s3 | 0.9330 | 0.9036 | +0.0294 |
| P2C5-final-s1 | 0.7986 | 0.7966 | +0.0020 |
| P2C5-final-s2 | 0.7879 | 0.7656 | +0.0223 |
| P2C5-final-s3 | 0.7978 | 0.7967 | +0.0011 |

Largest |board - pred|: 0.0302 (P2C4-final-s2); runs within 0.03: 14/15
