#!/usr/bin/env python3
"""Energy-deficit regime on the real Sentinel-2 traces (2026-10-13): the payload
board always on, the platform's available power (generation - bus, orbit
average) only a little above the board's idle power, the battery starting just
above the survival floor. Low effort is then sustainable and high effort is not;
the question is whether a policy trades effort for energy when deadlines are
short and waits to recharge for higher effort when deadlines allow.

    python3 analysis/lab_energy_deficit.py -o analysis/lab/energy_deficit.csv
    python3 analysis/lab_energy_deficit.py --summary analysis/lab/energy_deficit.csv

Policies: static low/med/high, deadline-only, v2 (capacity budget + fill
horizon, adopted 2026-10-13), v2 + E1 (energy feasibility of known work),
v2 + E3 (learned net-power guard), v2 + ET (energy-timed feasibility: recharge
time inserted into the deadline check).
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

CELLS = {"C1": ("main", "cloudy"), "C2": ("main", "matched"), "C3": ("main", "keepall"), "C4": ("main2x", "matched")}
ROUTINE = (30, 300, 1800, 3600, 21600)
URGENT = (20, 60)
# (name, sunlit fraction, power available to the payload W, board mode, start above floor Wh).
# Payload draw while busy: idle 3.30 W + modules ~0.6 W (low) .. ~1.15 W (high), i.e. ~3.9 / ~4.5 W.
# "dd" = dawn-dusk orbit (continuous sun) with the available power between those two draws, so
# low effort is sustainable and high effort is not; "disco" = the paper's DISCO-2 BOL regime as a
# control (imaging in sunlight, where the surplus exceeds any payload draw).
REGIMES = [("dd3.5", 1.0, 3.5, "always", 0.05), ("dd3.7", 1.0, 3.7, "always", 0.05),
           ("dd3.9", 1.0, 3.9, "always", 0.05), ("disco", 0.627, 3.37, "burst", 1.0)]
STATIC = ("static-low", "static-med", "static-high")
V2 = dict(guard="hist", accept="feas", boundary="backlog", cad_src="arrival", budget="frames",
          path_k=2.0, cap_budget=True, nohist="fill")

_content = None


PSET = os.environ.get("LAB_ENERGY_SET")      # a policy_lab set, e.g. final3-energy; unset = the list below


def policies():
    if PSET:
        return L.policy_set(PSET)
    return [L.Static("low"), L.Static("med"), L.Static("high"), L.Greedy(),
            L.Ladder(**V2, name="v2"), L.Ladder(**V2, energy_feas=True, name="v2+E1"),
            L.Ladder(**V2, energy_guard=True, name="v2+E3"), L.Ladder(**V2, energy_time=True, name="v2+ET"),
            L.Ladder(**V2, energy_service=True, name="v2+ES"),
            L.Ladder(**V2, energy_time=True, energy_service=True, name="v2+ET+ES"),
            L.Ladder(**V2, energy_time=True, energy_future=True, name="v2+ETF")]


def energy(regime, seed):
    name, sunfrac, surplus, board, above = [r for r in REGIMES if r[0] == regime][0]
    cap, bus, T = 92.0, 13.07, 5725.8                          # DISCO-2 design-report platform
    floor = 2 * bus * T / 3600.0
    return dict(cap=cap, floor=floor, w0=floor + above, bus=bus, p_sun=(bus + surplus) / sunfrac,
                sunfrac=sunfrac, t_orb=T, phase0=(seed * 0.37) % 1.0, p_idle=3.30, hold=60.0, mode=board)


def jobs():
    return [(du, dr, rg[0], c, sd) for du in URGENT for dr in ROUTINE if du < dr
            for rg in REGIMES for c in CELLS for sd in (1, 2, 3)]


def run(job):
    global _content
    if _content is None:
        _content = S.build_content()
    content, sets = _content
    du, dr, rg, cell, sd = job
    pre, ct = CELLS[cell]
    arr = S.read_trace(os.path.join(ROOT, "experiments/traces", f"{pre}-s{sd}.trace"))
    sc = scenario(arr, sets[ct], content, dr / 300.0, seed=stable_seed(cell, sd), slo_u=float(du))
    sc["energy"] = energy(rg, sd)
    sc["p_budget"] = [r for r in REGIMES if r[0] == rg][0][2]   # the platform's stated budget
    row = {"D_u": du, "D_r": dr, "regime": rg, "cell": cell, "seed": sd}
    for p in policies():
        r = L.simulate(sc, p)
        row[f"U:{p.name}"] = round(r["U"], 4)
        row[f"urg:{p.name}"] = round(r["urgent_hit"], 4)
        row[f"rout:{p.name}"] = round(r["routine_hit"], 4)
        row[f"fhigh:{p.name}"] = round(r["f_high"], 3)
        row[f"minW:{p.name}"] = round(r.get("minW", 0.0), 3)
    return row


def summary(path):
    R = list(csv.DictReader(open(path)))
    names = [k[2:] for k in R[0] if k.startswith("U:")]
    g = defaultdict(list)
    for r in R:
        g[(r["regime"], int(r["D_u"]), int(r["D_r"]))].append(r)
    print("mean U over cells x seeds (worst regret vs best static) | share of frames at high")
    for k in sorted(g):
        rs = g[k]
        line = f"{k[0]:6} Du={k[1]:2} Dr={k[2]:5}:"
        for n in names:
            u = statistics.mean(float(r[f"U:{n}"]) for r in rs)
            reg = max(max(float(r[f"U:{s}"]) for s in STATIC) - float(r[f"U:{n}"]) for r in rs)
            fh = statistics.mean(float(r[f"fhigh:{n}"]) for r in rs)
            line += f" {n} {u:.3f}({reg:+.2f})h{fh:.2f}"
        print(line)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o")
    ap.add_argument("-j", type=int, default=7)
    ap.add_argument("--summary")
    a = ap.parse_args()
    if a.summary:
        summary(a.summary)
        return 0
    js = jobs()
    print(f"{len(js)} jobs", flush=True)
    with mp.Pool(a.j) as pool:
        rows = list(pool.imap(run, js, chunksize=2))
    with open(a.o, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print("wrote", a.o)
    summary(a.o)
    return 0


if __name__ == "__main__":
    sys.exit(main())
