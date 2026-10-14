#!/usr/bin/env python3
"""Policy lab on the real Sentinel-2 traces with the content measured on the
board (cloudy / Earth-average / clear-sky), at several routine deadlines:
the evaluation cells C1-C5 (3 seeds each) and the full-day traces.

    python3 analysis/lab_real.py --set final -o analysis/lab/real_final.csv
"""
import argparse, csv, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import policy_lab as L  # noqa: E402
import sched_sim as S   # noqa: E402

SCR = "/tmp/claude-1000/-home-roba-dipp-paper/a3ec5c5c-e641-4328-b69b-4c8e2d6feef0/scratchpad/day"


def scenario(arr, frames, content, slo_f, hi=1.0, model_err=0.0, jitter=0.0, seed=0, slo_u=None):
    """slo_f scales the trace's routine deadlines; slo_u (s), when given, replaces
    the urgent deadline (otherwise the trace's own, 20 s)."""
    rows = []
    for i, (a, s, c) in enumerate(arr):
        ci = content[frames[i % len(frames)]]
        ns = int(round(ci["nseg"]["low"]))
        d = s * slo_f if c == "routine" else (s if slo_u is None else slo_u)
        rows.append((a, d, c, ns, int(round(ns * ci["nenc_ratio"]["low"]))))
    true = L.Cost(L.base_cost(), hi)
    sc = {"p": {"seed": seed, "period": 0}, "arrivals": rows, "slo_r": 300.0 * slo_f, "cost": true,
          "over": 0.30, "ingest_s": 0.41, "ckpt": 0.5, "window": 8, "jitter": jitter}
    if model_err:
        import math, random
        r = random.Random(seed * 31 + 7)
        sc["model"] = L.ModelCost(true, {sl: math.exp(r.gauss(0, model_err)) for sl in range(1, 21)})
    return sc


def stable_seed(*parts):
    """Seed from names, independent of PYTHONHASHSEED (hash() of str is not)."""
    import zlib
    return zlib.crc32("|".join(map(str, parts)).encode()) % 10000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="final")
    ap.add_argument("-o", required=True)
    a = ap.parse_args()
    content, sets = S.build_content()
    cells = {"C1": ("main", "cloudy"), "C2": ("main", "matched"), "C3": ("main", "keepall"),
             "C4": ("main2x", "matched"), "C5": ("main2x", "keepall")}
    jobs = []
    for c, (pre, ct) in cells.items():
        for sd in (1, 2, 3):
            jobs.append((c, f"s{sd}", os.path.join(ROOT, f"experiments/traces/{pre}-s{sd}.trace"), ct))
    for d, ct in (("day1x", "cloudy"), ("day1x", "matched"), ("day1x", "keepall"),
                  ("day2x", "matched"), ("day2x", "keepall")):
        p = os.path.join(SCR, f"{d}.trace")
        if os.path.exists(p):
            jobs.append((f"{d}-{ct}", "day", p, ct))
    out = []
    pols = L.policy_set(a.set)
    for slo_f in (0.5, 1.0, 2.0, 4.0):
        for cond, (me, jt) in (("exact", (0.0, 0.0)), ("err03", (0.3, 0.15))):
            for cell, seed, path, ct in jobs:
                arr = S.read_trace(path)
                sc = scenario(arr, sets[ct], content, slo_f, model_err=me, jitter=jt, seed=stable_seed(cell, seed))
                row = {"cell": cell, "seed": seed, "slo_r": 300 * slo_f, "cond": cond}
                for pol in pols:
                    r = L.simulate(sc, pol)
                    row[f"U:{pol.name}"] = round(r["U"], 4)
                    row[f"urg:{pol.name}"] = round(r["urgent_hit"], 4)
                out.append(row)
            print(f"slo x{slo_f} {cond} done", flush=True)
    with open(a.o, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader(); w.writerows(out)
    # summary: worst regret vs best static per (slo, cond), over cells
    print(f"\nworst regret vs best static, per routine deadline and model condition")
    names = [p.name for p in pols if not p.name.startswith("static")]
    for slo_f in (0.5, 1.0, 2.0, 4.0):
        for cond in ("exact", "err03"):
            rs = [r for r in out if r["slo_r"] == 300 * slo_f and r["cond"] == cond]
            line = f"  SLO {300*slo_f:5.0f} {cond:6}"
            for n in names:
                reg = [max(r[f"U:static-{e}"] for e in ("low", "med", "high")) - r[f"U:{n}"] for r in rs]
                line += f" {n}:{max(reg):.3f}"
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
