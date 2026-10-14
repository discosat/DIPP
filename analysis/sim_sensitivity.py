#!/usr/bin/env python3
"""Threshold sensitivity of the default scheduling policy, from the host
simulator (tools/sched_sim.py), on the evaluation's cells.

    python3 analysis/sim_sensitivity.py -o analysis/sim

Cells as in experiments/matrices/gen_eval.py (C1 1x cloudy, C2 1x matched,
C3 1x clear, C4 2x matched, C5 2x clear; seeds 1-3 of the main traces).
Caveat: the simulator's "cloudy" content is the six CloudSEN12 validation
frames whose per-effort tile counts were measured on the board; the bench
uses the natural test frames.

For k in a grid, Q1/Q2 follow METRIC.md's rule floor(300 / (k * s_e_max))
with s_high_max 12.15 s, s_medium_max 7.33 s; for each k the worst-case and
mean regret against the best static policy, and the worst urgent hit rate,
at stage and batch granularity. Also the original two-tier policy (Q 5/5,
batch) and deadline-greedy for reference. Writes k_sensitivity.csv.
"""
import argparse, csv, math, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import sched_sim as s  # noqa: E402

S_MAX = {"high": 12.15, "med": 7.33}
CELLS = {"C1": ("main", "cloudy"), "C2": ("main", "matched"), "C3": ("main", "keepall"),
         "C4": ("main2x", "matched"), "C5": ("main2x", "keepall")}
OVERHEAD_S, CKPT_S = 0.3, 0.5


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--outdir", required=True)
    a = ap.parse_args()
    content, sets = s.build_content()
    cost = s.Cost(os.path.join(ROOT, "analysis/cost_model.json"))
    TR = {pre: [s.read_trace(os.path.join(ROOT, f"experiments/traces/{pre}-s{i}.trace")) for i in (1, 2, 3)]
          for pre in ("main", "main2x")}

    def cell_stats(policy, gran, **kw):
        out = {}
        for c, (pre, ct) in CELLS.items():
            rs = [s.summarise(*s.simulate(tr, sets[ct], content, cost, policy, gran, OVERHEAD_S, CKPT_S, **kw)[:2])
                  for tr in TR[pre]]
            out[c] = (statistics.mean(r["all"]["U"] for r in rs), min(r["urgent"]["hit"] for r in rs))
        return out

    best = {c: max(cell_stats(p, "batch")[c][0] for p in ("static-low", "static-med", "static-high")) for c in CELLS}
    rows = []

    def add(name, k, policy, gran, **kw):
        st = cell_stats(policy, gran, **kw)
        rg = [best[c] - st[c][0] for c in CELLS]
        rows.append({"policy": name, "k": k, "q_med": kw.get("q1", kw.get("q")), "q_low": kw.get("q2", kw.get("q")),
                     "granularity": gran, "max_regret": round(max(rg), 4), "mean_regret": round(statistics.mean(rg), 4),
                     "min_urgent_hit": round(min(v[1] for v in st.values()), 3),
                     **{f"U_{c}": round(st[c][0], 4) for c in CELLS}})

    for k in (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0):
        q1 = math.floor(300 / (k * S_MAX["high"])); q2 = math.floor(300 / (k * S_MAX["med"]))
        for gran in ("stage", "batch"):
            add("ladder", k, "ladder", gran, q1=q1, q2=q2)
    add("original two-tier (Q 5/5)", None, "shipped", "batch", q=5)
    add("deadline-greedy", None, "greedy", "stage")
    for p in ("static-low", "static-med", "static-high"):
        add(p, None, p, "batch")
    os.makedirs(a.outdir, exist_ok=True)
    path = os.path.join(a.outdir, "k_sensitivity.csv")
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print(f"{'policy':28}{'k':>5}{'Q1/Q2':>8}{'gran':>7}{'maxReg':>8}{'meanReg':>9}{'minUrg':>8}  " +
          " ".join(f"{c:>6}" for c in CELLS))
    for r in rows:
        print(f"{r['policy']:28}{str(r['k'] or ''):>5}{str(r['q_med'] or '')+'/'+str(r['q_low'] or ''):>8}"
              f"{r['granularity']:>7}{r['max_regret']:8.3f}{r['mean_regret']:+9.3f}{r['min_urgent_hit']:8.2f}  " +
              " ".join(f"{r['U_'+c]:6.3f}" for c in CELLS))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
