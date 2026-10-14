#!/usr/bin/env python3
"""Provisioning for typical content, policy v2: every high-effort module made
1.5x, 2x and 3x as expensive (quality held fixed); the depth thresholds are
re-derived by the same rule (s_max from the cost model). Evaluation cells
C1-C5, seeds 1-3, exact cost model.

    python3 analysis/lab_high_scaling.py -o analysis/lab/high_scaling_v2.csv
"""
import argparse, csv, os, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, HERE)
import policy_lab as L  # noqa: E402
import sched_sim as S   # noqa: E402
from lab_real import scenario  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", required=True)
    ap.add_argument("--set", default="sens2")
    ap.add_argument("--policy", default="v2", help="the policy whose regret is summarised")
    a = ap.parse_args()
    content, sets = S.build_content()
    cells = {"C1": ("main", "cloudy"), "C2": ("main", "matched"), "C3": ("main", "keepall"),
             "C4": ("main2x", "matched"), "C5": ("main2x", "keepall")}
    pols = L.policy_set(a.set)
    out = []
    for hi in (1.0, 1.5, 2.0, 3.0):
        for c, (pre, ct) in cells.items():
            for sd in (1, 2, 3):
                arr = S.read_trace(os.path.join(ROOT, f"experiments/traces/{pre}-s{sd}.trace"))
                sc = scenario(arr, sets[ct], content, 1.0, hi=hi, seed=sd)
                row = {"hi": hi, "cell": c, "seed": sd}
                for p in pols:
                    r = L.simulate(sc, p)
                    row[f"U:{p.name}"] = round(r["U"], 4)
                    row[f"urg:{p.name}"] = round(r["urgent_hit"], 4)
                out.append(row)
        print(f"hi x{hi} done", flush=True)
    with open(a.o, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
    print("\nmean U over seeds; worst regret of v2 vs best static over cells")
    for hi in (1.0, 1.5, 2.0, 3.0):
        rs = [r for r in out if r["hi"] == hi]
        line = f"x{hi}:"
        for c in cells:
            cr = [r for r in rs if r["cell"] == c]
            m = lambda n: sum(r[f"U:{n}"] for r in cr) / len(cr)
            line += f"  {c} v2 {m(a.policy):.3f} hi {m('static-high'):.3f} med {m('static-med'):.3f}"
        reg = max(max(r[f"U:static-{e}"] for e in ("low", "med", "high")) - r[f"U:{a.policy}"] for r in rs)
        urg = min(r[f"urg:{a.policy}"] for r in rs)
        print(line + f"  | worst regret {reg:.3f} urgent min {urg:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
