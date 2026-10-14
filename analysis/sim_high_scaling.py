#!/usr/bin/env python3
"""Discussion support (plan, "Later"): what if the high rung were more
expensive -- a larger model than the board's NPU currently fits at full rate?
Host simulator only (tools/sched_sim.py), on the evaluation's cells.

    python3 analysis/sim_high_scaling.py -o analysis/sim

Every high-effort slot's latency (classification 5, segmentation 7, encoder
18) is multiplied by f; the ladder's thresholds follow the same rule as the
evaluated build, Q_e = floor(300 / (2 * s_e_max)), with s_high_max recomputed
from the scaled cost model on keep-all content. Quality of high stays 1: the
question is only whether the policy can still use an expensive top rung
where it fits, not how much quality such a model would add. Writes
high_scaling.csv: per f, per cell, U of static medium / static high / the
ladder (stage granularity), mean over seeds 1-3, and the ladder's regret.
"""
import argparse, csv, math, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import sched_sim as s  # noqa: E402

CELLS = {"C1": ("main", "cloudy"), "C2": ("main", "matched"), "C3": ("main", "keepall"),
         "C4": ("main2x", "matched"), "C5": ("main2x", "keepall")}
OVERHEAD_S, CKPT_S, SLO_ROUTINE, K = 0.3, 0.5, 300, 2.0
HIGH_SLOTS = (5, 7, 18)
S_MED_MAX = 7.33          # medium is not scaled (METRIC.md)


class ScaledCost(s.Cost):
    def __init__(self, path, f):
        super().__init__(path)
        self.f = f

    def lat(self, slot, n):
        v = super().lat(slot, n)
        return v * self.f if slot in HIGH_SLOTS else v


def s_high_max(cost):
    return (cost.lat(1, 1) + cost.lat(2, 1) + cost.lat(5, 99) + cost.lat(7, 99) + cost.lat(18, 99)
            + OVERHEAD_S)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--factors", default="1,1.5,2,3")
    a = ap.parse_args()
    content, sets = s.build_content()
    path = os.path.join(ROOT, "analysis/cost_model.json")
    TR = {pre: [s.read_trace(os.path.join(ROOT, f"experiments/traces/{pre}-s{i}.trace")) for i in (1, 2, 3)]
          for pre in ("main", "main2x")}
    base = s_high_max(s.Cost(path))
    print(f"s_high_max at f=1: {base:.2f} s (METRIC.md: 12.15 s)")
    rows = []
    for f in (float(x) for x in a.factors.split(",")):
        cost = ScaledCost(path, f)
        shm = s_high_max(cost)
        q1 = math.floor(SLO_ROUTINE / (K * shm)); q2 = math.floor(SLO_ROUTINE / (K * S_MED_MAX))
        for c, (pre, ct) in CELLS.items():
            U = {}
            for pol, gran, kw in (("static-low", "batch", {}), ("static-med", "batch", {}),
                                  ("static-high", "batch", {}), ("ladder", "stage", {"q1": q1, "q2": q2})):
                us = [s.summarise(*s.simulate(tr, sets[ct], content, cost, pol, gran, OVERHEAD_S, CKPT_S, **kw)[:2])
                      ["all"]["U"] for tr in TR[pre]]
                U[pol] = statistics.mean(us)
            best = max(U["static-low"], U["static-med"], U["static-high"])
            rows.append({"f": f, "s_high_max": round(shm, 2), "q1": q1, "q2": q2, "cell": c,
                         "U_low": round(U["static-low"], 4), "U_med": round(U["static-med"], 4),
                         "U_high": round(U["static-high"], 4), "U_ladder": round(U["ladder"], 4),
                         "ladder_minus_med": round(U["ladder"] - U["static-med"], 4),
                         "regret_ladder": round(best - U["ladder"], 4)})
    os.makedirs(a.outdir, exist_ok=True)
    out = os.path.join(a.outdir, "high_scaling.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print(f"{'f':>4} {'s_hi':>6} {'Q1/Q2':>6} {'cell':>4} {'low':>6} {'med':>6} {'high':>6} {'ladder':>7} {'lad-med':>8} {'regret':>7}")
    for r in rows:
        print(f"{r['f']:4} {r['s_high_max']:6} {str(r['q1'])+'/'+str(r['q2']):>6} {r['cell']:>4} {r['U_low']:6.3f} "
              f"{r['U_med']:6.3f} {r['U_high']:6.3f} {r['U_ladder']:7.3f} {r['ladder_minus_med']:+8.3f} {r['regret_ladder']:7.3f}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
