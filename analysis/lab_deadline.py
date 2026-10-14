#!/usr/bin/env python3
"""Grounded deadline sweep (2026-10-13): every policy on the real Sentinel-2
traces at urgent and routine deadlines taken from published missions, and at
several queue capacities.

    python3 analysis/lab_deadline.py -o analysis/lab/deadline.csv
    python3 analysis/lab_deadline.py --summary analysis/lab/deadline.csv

Urgent deadlines (s): 10 (near Dynamic Targeting's 6 s analysis cap, below
SIPP's 17.9 s bound), 20 (SIPP's bound, rounded), 60 (Dynamic Targeting's
decision loop), 300 (alert, Phoenix). Routine deadlines (s): 30 (Kodan), 300
(Phoenix), 1800 (CleanSeaNet), 3600, 21600 and 40320 (median and maximum wait
for the next DISCO-2 contact). Only pairs with urgent < routine. Queue
capacity 100 everywhere; 200 and 300 (the eMMC ceiling) where the routine
deadline is 1800 s or more, the only place the capacity can bind. Plus the
no-urgent trace (main-urgent0-s1) at the two contact deadlines.

Cells C1-C5 as in the board evaluation, seeds 1-3 (target placement), cost
model exact and with error (sigma 0.3 per configuration, 0.15 jitter).
Policies: static low/med/high, deadline-only (greedy), v1
(ladder-as-evaluated), v2, v2 with k 1.5 and 3.
"""
import argparse, csv, multiprocessing as mp, os, statistics, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, HERE)
import policy_lab as L  # noqa: E402
import sched_sim as S   # noqa: E402
from lab_real import scenario, stable_seed  # noqa: E402

URGENT = (10, 20, 60, 300)
ROUTINE = (30, 300, 1800, 3600, 21600, 40320)
CAPS_LONG = (100, 200, 300)
CELLS = {"C1": ("main", "cloudy"), "C2": ("main", "matched"), "C3": ("main", "keepall"),
         "C4": ("main2x", "matched"), "C5": ("main2x", "keepall")}
CONDS = {"exact": (0.0, 0.0), "err03": (0.3, 0.15)}
STATIC = ("static-low", "static-med", "static-high")

_content = None
PSET = "sens2"


def policies():
    ps = L.policy_set(PSET)
    return ps if any(p.name == "greedy" for p in ps) else ps + [L.Greedy()]


def jobs():
    out = []
    for du in URGENT:
        for dr in ROUTINE:
            if du >= dr:
                continue
            for cap in (CAPS_LONG if dr >= 1800 else (100,)):
                for cell, (pre, ct) in CELLS.items():
                    for sd in (1, 2, 3):
                        for cond in CONDS:
                            out.append((du, dr, cap, cell, f"{pre}-s{sd}", ct, sd, cond))
    for dr in (21600, 40320):                    # store-and-forward without urgent frames
        for cap in CAPS_LONG:
            for cond in CONDS:
                out.append(("none", dr, cap, "C2u0", "main-urgent0-s1", "matched", 1, cond))
    return out


def run(job):
    global _content, PSET
    PSET = job[-1]
    job = job[:-1]
    if _content is None:
        _content = S.build_content()
    content, sets = _content
    du, dr, cap, cell, trace, ct, sd, cond = job
    L.MAX_INGEST = cap                           # both queues share the daemon's MAX_QUEUE_SIZE
    arr = S.read_trace(os.path.join(ROOT, "experiments/traces", trace + ".trace"))
    me, jt = CONDS[cond]
    sc = scenario(arr, sets[ct], content, dr / 300.0, model_err=me, jitter=jt,
                  seed=stable_seed(cell, sd), slo_u=None if du == "none" else float(du))
    row = {"D_u": du, "D_r": dr, "cap": cap, "cell": cell, "seed": sd, "cond": cond}
    for p in policies():
        r = L.simulate(sc, p)
        row[f"U:{p.name}"] = round(r["U"], 4)
        row[f"urg:{p.name}"] = round(r["urgent_hit"], 4)
        row[f"rout:{p.name}"] = round(r["routine_hit"], 4)
        row[f"capdrop:{p.name}"] = r["cap_dropped"]
        row[f"undone:{p.name}"] = r["dropped"]
    return row


def summary(path):
    R = list(csv.DictReader(open(path)))
    f = lambda r, k: float(r[k])
    names = [k[2:] for k in R[0] if k.startswith("U:")]
    groups = defaultdict(list)
    for r in R:
        groups[(r["D_u"], int(r["D_r"]), int(r["cap"]), r["cond"])].append(r)
    print("per (urgent, routine, cap, cond): best static per cell (seed 1), worst/mean regret, v2 urgent min, cap drops")
    for (du, dr, cap, cond), rs in sorted(groups.items(), key=lambda x: (x[0][3], str(x[0][0]).zfill(4), x[0][1], x[0][2])):
        best = {}
        for c in sorted({r["cell"] for r in rs}):
            r1 = [r for r in rs if r["cell"] == c and r["seed"] == "1"][0]
            best[c] = max(STATIC, key=lambda s: f(r1, f"U:{s}")).split("-")[1]
        reg = {n: [max(f(r, f"U:{s}") for s in STATIC) - f(r, f"U:{n}") for r in rs] for n in names}
        line = f"{cond:5} Du={du:>4} Dr={dr:>5} cap={cap:3} best[{' '.join(f'{c}:{b}' for c, b in best.items())}]"
        for n in [x for x in ("sipp", "v2-board", "v2", "ladder-as-evaluated", "greedy", "v2-k1.5", "v2-k3") if x in names]:
            line += f" {n.replace('ladder-as-evaluated', 'v1')}:{max(reg[n]):.3f}/{statistics.mean(reg[n]):+.3f}"
        main = "sipp" if "sipp" in names else "v2"
        line += f" | {main} urg min {min(f(r, f'urg:{main}') for r in rs):.2f} lowurg {min(f(r, 'urg:static-low') for r in rs):.2f}"
        line += f" | capdrop {main} {max(int(r[f'capdrop:{main}']) for r in rs)} high {max(int(r['capdrop:static-high']) for r in rs)}"
        print(line)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o")
    ap.add_argument("-j", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--summary")
    ap.add_argument("--set", default="sens2", help="policy_lab policy set (greedy is added if missing)")
    a = ap.parse_args()
    if a.summary:
        summary(a.summary)
        return 0
    if not a.o:
        ap.error("-o or --summary is required")
    js = [j + (a.set,) for j in jobs()]
    print(f"{len(js)} jobs on {a.j} processes", flush=True)
    rows = []
    with mp.Pool(a.j) as pool:
        for i, r in enumerate(pool.imap(run, js, chunksize=2)):
            rows.append(r)
            if (i + 1) % 100 == 0:
                print(f"  {i + 1}/{len(js)}", flush=True)
    with open(a.o, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print("wrote", a.o)
    summary(a.o)
    return 0


if __name__ == "__main__":
    sys.exit(main())
