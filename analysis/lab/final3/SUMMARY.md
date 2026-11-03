# Simulation evidence for the frozen policy (2026-10-14)

Policy "sipp" (policy_lab set `final3`) = the board-evaluated v2 ("v2-board") + capacity-aware budget + queue-fill horizon + energy-timed feasibility with the wait rule. Definition: `paper-rev/policy_final.md`. Driver: `analysis/lab/run_final3.sh`; log `analysis/lab/final3.log`. Quote-once seed ranges: random 300000+, grid 400000+, random energy 500000+, adversary starts 600000+. "Regret" = best static U − policy U (lower is better).

## Random scenarios (deadlines 1–40 min unless stated)

| Set | sipp p99 / max | v2-board p99 / max | v1 p99 / max | static low |
|---|---|---|---|---|
| Held-out, 3,000 | 0.073 / 0.180 | 0.073 / 0.180 | 0.081 / 0.184 | 0.209 |
| Irregular arrivals cv 0.3 | 0.070 / 0.181 | 0.066 / 0.181 | 0.078 / 0.254 | 0.209 |
| Irregular arrivals cv 1.0 (Poisson) | 0.067 / 0.110 | 0.067 / 0.110 | 0.079 / 0.181 | 0.209 |
| Routine deadline 6 h | **0.046 / 0.096** | 0.456 / 0.585 | 0.497 / 0.585 | 0.209 |
| Urgent deadline 10 s | 0.063 / 0.138 | 0.064 / 0.138 | 0.081 / 0.183 | 0.209 |
| Urgent deadline 60 s | 0.078 / 0.124 | 0.078 / 0.124 | 0.092 / 0.166 | 0.209 |

Policy-caused urgent misses (worst scenario where static low meets every urgent deadline): sipp 0.75 held-out (one scenario, seed 302745; sipp better than v2-board in 27 scenarios and worse in 5 of 2,053), 0.86–0.91 in the other sets; 5th percentile 1.00.

## Sensitivity (1,500 scenarios)

| Variant | Mean | p99 | Max |
|---|---|---|---|
| sipp (k 2, window 8, overhead 0.78 s) | −0.009 | 0.068 | 0.134 |
| k = 1.5 | −0.012 | 0.057 | 0.182 |
| k = 3 | −0.002 | 0.091 | 0.141 |
| Window 4 / 16 | −0.006 / −0.010 | 0.069 / 0.075 | 0.120 / 0.137 |
| Overhead ×0.5 / ×1.5 | −0.009 / −0.009 | 0.077 / 0.072 | 0.157 / 0.120 |

## Regime grid (1,200 cells, deadlines 1 min – 6 h, burst 5–400, period 2.2–10 s, 3 contents)

| Policy | Worst | Share > 0.05 |
|---|---|---|
| sipp | 0.125 | 7.0 % |
| v2-board | 0.272 | 9.1 % |
| v1 | 0.277 | 11.3 % |
| deadline-only | 0.749 | 22.7 % |

Per deadline, sipp worst 0.10–0.125 everywhere; v2-board 0.04 at 20 min but 0.27 at 2–6 h.

## Adversarial search (32 chains × 60 steps, deadlines up to 6 h)

Worst cases have 25–40 % urgent frames under overload; static low is best there. Re-simulated: sipp 0.214 / 0.283 / 0.216, identical to v2-board; v1 0.221 / 0.299 / 0.251. So the adaptive policies' worst case (≈ 0.28) is slightly above static low's constant 0.209, in heavy-urgent overload. The earlier search (narrower space, older policy) found 0.201.

## High-cost model (×1–3, real traces)

sipp worst regret 0.043–0.045 at every factor (= v2-board); urgent min 1.00 (0.96 at ×3).

## Real traces: grounded deadline sweep (`deadline.csv`, figures `analysis/fig/final3/deadline_sweep.pdf`, `urgent_sweep.pdf`)

Queue capacity 100, exact cost model, worst regret over 5 cells × 3 seeds:

| Urgent / routine | sipp | v2-board | v1 | deadline-only |
|---|---|---|---|---|
| 20 s / 30 s | 0.051 | 0.051 | 0.051 | 0.617 |
| 20 s / 5 min | 0.045 | 0.045 | 0.109 | 0.643 |
| 20 s / 30 min – 11.2 h | 0.029 | 0.010–0.012 | 0.043–0.134 | 0.134 |
| 60–300 s / 30 min – 11.2 h | 0.044 | 0.031–0.044 | 0.036–0.126 | 0.124–0.126 |

sipp: no queue drops and urgent hit 1.00 for urgent deadlines ≥ 20 s at capacity 100 (v2-board: up to 20 drops, urgent 0.95). Cost of the capacity budget: about +0.02 worst-case regret at long deadlines on these traces.

## Energy

Random energy scenarios (2,000; 425 where energy binds), binding subset p99: sipp 0.104, v2-board 0.130, static low 0.209. In these generic regimes ET itself changes little (sipp vs sipp without ET: equal); the gain is from the capacity budget and fill horizon.

Real traces, deficit regimes (dawn–dusk, board always on, start 0.05 Wh above the floor; worst / mean of U − best static, higher is better):

| Regime / deadline | sipp | sipp without ET | sipp + E1 | v2-board |
|---|---|---|---|---|
| 3.5 W / 300 s | −0.299 / −0.141 | −0.303 / −0.142 | **−0.165 / −0.047** | −0.303 / −0.142 |
| 3.5 W / 30 min – 6 h | **−0.062…−0.077 / +0.003…−0.016** | −0.080…−0.087 / −0.019…−0.049 | −0.084…−0.103 | −0.080…−0.104 |
| 3.7 W / 30 min – 6 h | **−0.049 / +0.001…+0.007** | −0.075 / −0.028 | −0.077 / −0.025 | −0.055…−0.058 / −0.024 |
| 3.9 W / 30 min – 6 h | **−0.026…−0.050 / +0.005…+0.006** | −0.044…−0.067 / −0.013 | −0.059…−0.084 | −0.040…−0.060 |
| DISCO / 1 h | −0.097 / −0.031 | −0.097 / −0.031 | −0.102 | **−0.092 / −0.023** |
| DISCO / 6 h | −0.081 / −0.021 | −0.082 / −0.021 | −0.081 | **−0.007 / +0.005** |

Timeline: `analysis/fig/final3/energy_timeline.pdf` (3.7 W, 1× clear sky): at 30 s sipp trades effort for energy (0 of 222 late; static high 211 late); at 6 h it waits at the floor and then runs high effort (U 0.977 vs 0.903 without ET).

## Open points for the paper

1. Tight case (3.5 W, nearly empty battery, board always on, 300 s): only E1 handles it; stated as a limitation.
2. DISCO regime at 6 h: the capacity budget costs 0.07 in the worst cell against v2-board (−0.081 vs −0.007), with the eclipse-phase start; the price of removing the 6 h collapse in random scenarios (p99 0.046 vs 0.456).
3. Adversarial worst case ≈ 0.28 (heavy urgent overload) exceeds static low's 0.209 for every adaptive policy.
4. Simulator accuracy: within 0.03 of the board for v2; static high and deadline-only up to 0.08 pessimistic on 1× clear sky.
