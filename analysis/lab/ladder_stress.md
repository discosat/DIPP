# Lower k under stress (jitter, cost-model error) — simulation

Protocol: ladder_protocol.md (addendum: stress test). Means over seeds 1-3, 17 cells.
Δ vs frozen = against sipp at k = 2 under the same stress; tie |Δ| ≤ 0.02.
Urgent hit: lowest over cells excluding P1C5 (infeasible for every policy).

## nominal

| policy | k | mean U | W/T/L vs frozen | worst Δ (cell) | min urgent hit (cell) | urgent misses | dropped |
|---|---|---|---|---|---|---|---|
| sipp | 1.25 | 0.893 | 1/16/0 | -0.003 (P2C5) | 0.958 (E37P2C3) | 139 | 143 |
| sipp | 1.5 | 0.891 | 0/17/0 | -0.005 (P2C5) | 0.984 (E37P2C3) | 137 | 143 |
| sipp | 1.75 | 0.890 | 0/17/0 | -0.000 (E37P4C3) | 0.988 (E37P2C3) | 137 | 141 |
| sipp | 2.0 | 0.889 | 0/17/0 | +0.000 (E37P2C2) | 1.000 (E37P2C2) | 136 | 141 |
| sipp-ladder | 1.25 | 0.898 | 2/15/0 | -0.003 (P1C5) | 0.972 (E37P2C3) | 141 | 161 |
| sipp-ladder | 1.5 | 0.897 | 3/14/0 | -0.005 (P2C5) | 0.978 (P3C5) | 140 | 161 |
| sipp-ladder | 1.75 | 0.894 | 2/15/0 | -0.007 (E37P2C2) | 0.978 (P3C5) | 139 | 159 |
| sipp-ladder | 2.0 | 0.891 | 0/17/0 | -0.009 (E37P2C2) | 0.978 (P3C5) | 139 | 159 |

## jitter0.15

| policy | k | mean U | W/T/L vs frozen | worst Δ (cell) | min urgent hit (cell) | urgent misses | dropped |
|---|---|---|---|---|---|---|---|
| sipp | 1.25 | 0.893 | 1/16/0 | +0.000 (E37P4C2) | 0.958 (E37P2C3) | 138 | 141 |
| sipp | 1.5 | 0.891 | 0/17/0 | +0.000 (E37P4C2) | 1.000 (E37P2C2) | 135 | 141 |
| sipp | 1.75 | 0.890 | 0/17/0 | -0.000 (E37P4C3) | 0.986 (E37P2C3) | 136 | 140 |
| sipp | 2.0 | 0.889 | 0/17/0 | +0.000 (E37P2C2) | 0.984 (E37P2C3) | 136 | 140 |
| sipp-ladder | 1.25 | 0.899 | 2/15/0 | -0.001 (P1C5) | 0.987 (P3C5) | 138 | 160 |
| sipp-ladder | 1.5 | 0.898 | 3/14/0 | -0.004 (E37P2C2) | 0.987 (P3C5) | 137 | 160 |
| sipp-ladder | 1.75 | 0.894 | 2/15/0 | -0.007 (E37P2C2) | 0.974 (E37P2C3) | 139 | 159 |
| sipp-ladder | 2.0 | 0.891 | 0/17/0 | -0.010 (E37P2C2) | 0.987 (P3C5) | 137 | 159 |

## model0.3

| policy | k | mean U | W/T/L vs frozen | worst Δ (cell) | min urgent hit (cell) | urgent misses | dropped |
|---|---|---|---|---|---|---|---|
| sipp | 1.25 | 0.888 | 5/12/0 | -0.008 (P1C5) | 0.959 (E37P2C3) | 141 | 143 |
| sipp | 1.5 | 0.887 | 3/14/0 | -0.005 (P1C5) | 1.000 (E37P2C2) | 138 | 143 |
| sipp | 1.75 | 0.882 | 3/14/0 | -0.001 (E37P4C3) | 1.000 (E37P2C2) | 137 | 141 |
| sipp | 2.0 | 0.874 | 0/17/0 | +0.000 (E37P2C2) | 1.000 (E37P2C2) | 137 | 141 |
| sipp-ladder | 1.25 | 0.896 | 7/10/0 | -0.009 (P1C5) | 0.943 (E37P2C3) | 145 | 144 |
| sipp-ladder | 1.5 | 0.894 | 6/11/0 | -0.007 (P1C5) | 0.976 (E37P2C3) | 140 | 144 |
| sipp-ladder | 1.75 | 0.891 | 6/11/0 | -0.009 (E37P2C2) | 1.000 (E37P2C2) | 138 | 143 |
| sipp-ladder | 2.0 | 0.889 | 5/12/0 | -0.011 (E37P2C2) | 0.986 (E37P2C3) | 139 | 142 |

## both

| policy | k | mean U | W/T/L vs frozen | worst Δ (cell) | min urgent hit (cell) | urgent misses | dropped |
|---|---|---|---|---|---|---|---|
| sipp | 1.25 | 0.889 | 5/12/0 | -0.007 (P1C5) | 0.974 (E37P2C3) | 139 | 141 |
| sipp | 1.5 | 0.887 | 3/14/0 | -0.005 (P1C5) | 1.000 (E37P2C2) | 137 | 141 |
| sipp | 1.75 | 0.883 | 3/14/0 | -0.000 (E37P4C3) | 1.000 (E37P2C2) | 136 | 140 |
| sipp | 2.0 | 0.874 | 0/17/0 | +0.000 (E37P2C2) | 1.000 (E37P2C2) | 136 | 140 |
| sipp-ladder | 1.25 | 0.896 | 7/10/0 | -0.008 (P1C5) | 0.921 (E37P2C3) | 148 | 141 |
| sipp-ladder | 1.5 | 0.895 | 6/11/0 | -0.005 (P1C5) | 1.000 (E37P2C2) | 137 | 141 |
| sipp-ladder | 1.75 | 0.891 | 6/11/0 | -0.009 (E37P2C2) | 1.000 (E37P2C2) | 137 | 141 |
| sipp-ladder | 2.0 | 0.889 | 5/12/0 | -0.012 (E37P2C2) | 0.986 (E37P2C3) | 137 | 141 |

## optimistic0.8

| policy | k | mean U | W/T/L vs frozen | worst Δ (cell) | min urgent hit (cell) | urgent misses | dropped |
|---|---|---|---|---|---|---|---|
| sipp | 1.25 | 0.893 | 2/14/1 | -0.043 (P2C5) | 0.826 (P1C3) | 154 | 164 |
| sipp | 1.5 | 0.894 | 1/16/0 | -0.012 (P1C5) | 0.929 (E37P2C3) | 143 | 146 |
| sipp | 1.75 | 0.890 | 0/17/0 | +0.000 (E37P4C2) | 1.000 (E37P2C2) | 137 | 143 |
| sipp | 2.0 | 0.889 | 0/17/0 | +0.000 (E37P2C2) | 0.988 (E37P2C3) | 138 | 143 |
| sipp-ladder | 1.25 | 0.895 | 2/14/1 | -0.043 (P2C5) | 0.826 (P1C3) | 160 | 176 |
| sipp-ladder | 1.5 | 0.896 | 2/15/0 | -0.012 (P1C5) | 0.979 (P3C5) | 141 | 158 |
| sipp-ladder | 1.75 | 0.895 | 2/15/0 | -0.009 (E37P2C2) | 0.979 (P3C5) | 141 | 158 |
| sipp-ladder | 2.0 | 0.891 | 1/16/0 | -0.013 (P2C3) | 0.979 (P3C5) | 141 | 155 |

## Decision rule (no loss beyond the tie line in any cell, no lower urgent hit, at every stress level)

- sipp k=1.25: fails at nominal, jitter0.15, model0.3, both, optimistic0.8
- sipp k=1.5: fails at nominal, optimistic0.8
- sipp k=1.75: fails at nominal
- sipp k=2.0: PASSES
- sipp-ladder k=1.25: fails at nominal, model0.3, both, optimistic0.8
- sipp-ladder k=1.5: fails at nominal, model0.3, optimistic0.8
- sipp-ladder k=1.75: fails at nominal, jitter0.15, optimistic0.8
- sipp-ladder k=2.0: fails at nominal, model0.3, both, optimistic0.8
