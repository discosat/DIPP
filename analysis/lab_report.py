#!/usr/bin/env python3
"""Summarise a policy_lab sweep: regret of each policy against the best
static policy per scenario (METRIC.md), its distribution, urgent hit rates,
and where each policy fails (scenario features binned).

    python3 analysis/lab_report.py analysis/lab/sweep1.csv [--focus POLICY] [--top N]
"""
import argparse, csv, math, statistics, sys
from collections import defaultdict

STATICS = ("static-low", "static-med", "static-high")


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))]


def load(path):
    rows = list(csv.DictReader(open(path)))
    pols = [k[2:] for k in rows[0] if k.startswith("U:")]
    for r in rows:
        for k, v in list(r.items()):
            try:
                r[k] = float(v)
            except (TypeError, ValueError):
                pass
        best = max(r[f"U:{s}"] for s in STATICS)
        r["best_static"] = best
        top = max(r[f"U:{p}"] for p in pols)
        for p in pols:
            r[f"R:{p}"] = best - r[f"U:{p}"]          # regret vs best static
            r[f"G:{p}"] = top - r[f"U:{p}"]           # gap to the best policy in the pool
    return rows, pols


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--focus", default="")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--where", default="", help="python filter on row r, e.g. 'r[\"slo_r\"]<300'")
    a = ap.parse_args()
    rows, pols = load(a.csv)
    if a.where:
        rows = [r for r in rows if eval(a.where)]
    print(f"{len(rows)} scenarios\n")
    print(f"{'policy':26}{'maxR':>7}{'p99R':>7}{'p95R':>7}{'meanR':>7}{'>0.05':>7}{'meanGap':>8}{'p95Gap':>8}{'minUrg*':>7}{'p5Urg*':>7}{'meanU':>7}")
    for p in sorted(pols, key=lambda p: pct([r[f"R:{p}"] for r in rows], .99)):
        R = [r[f"R:{p}"] for r in rows]
        G = [r[f"G:{p}"] for r in rows]
        # policy-caused urgent misses: only where static low meets every urgent deadline
        Ug = [r[f"urg:{p}"] for r in rows if r["urgent"] > 0 and r["urg:static-low"] >= 0.999]
        print(f"{p:26}{max(R):7.3f}{pct(R,.99):7.3f}{pct(R,.95):7.3f}{statistics.mean(R):7.3f}"
              f"{sum(x > 0.05 for x in R) / len(R):7.3f}{statistics.mean(G):8.3f}{pct(G,.95):8.3f}"
              f"{min(Ug) if Ug else 1:7.2f}{pct(Ug,.05) if Ug else 1:7.2f}{statistics.mean(r[f'U:{p}'] for r in rows):7.3f}")
    if a.focus:
        p = a.focus
        print(f"\nworst {a.top} scenarios for {p}:")
        keys = ["seed", "period", "burst_mean", "burst_cv", "gap", "slo_r", "urgent", "water", "clear",
                "content_var", "hi_scale", "rho_low", "rho_med", "rho_high", "pressure_low", "pressure_high"]
        for r in sorted(rows, key=lambda r: -r[f"R:{p}"])[: a.top]:
            bs = max(STATICS, key=lambda s: r[f"U:{s}"])
            print(f"  R={r[f'R:{p}']:.3f} U={r[f'U:{p}']:.3f} best={bs}:{r['best_static']:.3f} | " +
                  " ".join(f"{k}={r[k]:.3g}" if isinstance(r[k], float) else f"{k}={r[k]}" for k in keys))
        print(f"\nmean regret of {p} by feature bin:")
        for feat, edges in (("rho_low", [0, .5, .8, 1, 1.2, 1.5, 2, 99]),
                            ("pressure_low", [0, 1e-9, .25, .5, 1, 2, 99]),
                            ("pressure_high", [0, 1e-9, .5, 1, 2, 4, 99]),
                            ("slo_r", [0, 120, 300, 600, 1200, 9999]),
                            ("burst_mean", [0, 10, 30, 100, 300, 9999]),
                            ("urgent", [-1, 0.01, 0.15, 1])):
            parts = []
            for lo, hi in zip(edges, edges[1:]):
                v = [r[f"R:{p}"] for r in rows if lo <= r[feat] < hi]
                if v:
                    parts.append(f"[{lo:g},{hi:g}):{statistics.mean(v):.3f}/max{max(v):.2f} n{len(v)}")
            print(f"  {feat:14} " + "  ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
