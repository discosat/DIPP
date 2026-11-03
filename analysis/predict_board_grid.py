#!/usr/bin/env python3
"""Simulated prediction for every run of the board grid, written BEFORE the
board runs (the board tests the simulator; nothing is tuned to the board).

    python3 analysis/predict_board_grid.py -o analysis/board_grid_predictions.csv

Each run of experiments/matrices/board-grid.yaml is simulated with
tools/policy_lab.py (policy set final3: static-low/med/high, greedy, sipp) on
its own trace, content and deadlines; block D with the dd3.7 energy regime
(continuous sun, 3.7 W, board always on, start 0.05 Wh above the floor).
"""
import argparse, csv, multiprocessing as mp, os, sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, HERE)
import policy_lab as L  # noqa: E402
import sched_sim as S   # noqa: E402
import lab_energy_deficit as E  # noqa: E402
from lab_real import scenario, stable_seed  # noqa: E402

LAB = {"low": "static-low", "med": "static-med", "high": "static-high", "greedy": "greedy", "final": "sipp"}
CONTENT = {"cs12_test_[0-9]": "cloudy", "cs12_test_matched_": "matched", "cs12_test_matchedkeep_": "keepall"}
_content = None


def build_scenario(r):
    """Simulator scenario of one board-grid run (also used by analysis/oracle.py).
    Returns (sc, lab, policy, seed, slos)."""
    global _content
    if _content is None:
        _content = S.build_content()
    content, sets = _content
    lab, rest = r["label"].split("-", 1)
    policy, seed = rest.rsplit("-s", 1)
    arr = S.read_trace(os.path.join(ROOT, r["arrivals"]))
    slos = {}
    for line in open(os.path.join(ROOT, r["arrivals"])):
        if not line.startswith("#"):
            p = line.split()
            slos[p[5]] = float(p[4])
    # The pair traces already carry the pair's routine deadline in each frame
    # (read_trace), so the frames are NOT scaled (slo_f = 1); only the policy's
    # routine-SLO parameter is set to the pair's value. Fixed 2026-10-16: the
    # first version scaled again by routine/300 (P1 frames got 3 s, P3 10800 s,
    # P4 1555200 s; P2 unaffected) -- METRIC.md addendum 2026-10-16.
    sc = scenario(arr, sets[CONTENT[r["image_prefix"]]], content, 1.0,
                  seed=stable_seed(lab, seed), slo_u=slos.get("urgent"))
    sc["slo_r"] = slos["routine"]
    assert all(abs(d - (slos[c])) < 1e-9 for (_, d, c, _, _) in sc["arrivals"]), "frame deadlines != trace"
    if lab.startswith("E37"):
        en = E.energy("dd3.7", int(seed))
        floor = en["floor"]
        en["w0"] = r["soc0"] * en["cap"]
        assert abs(en["w0"] - (floor + 0.05)) < 0.01, (en["w0"], floor)
        sc["energy"], sc["p_budget"] = en, 3.7
    return sc, lab, policy, seed, slos


def predict(r):
    sc, lab, policy, seed, slos = build_scenario(r)
    pol = [p for p in L.policy_set("final3") if p.name == LAB[policy]][0]
    out = L.simulate(sc, pol)
    return {"label": r["label"], "policy": policy, "slo_urgent": slos.get("urgent", ""),
            "slo_routine": slos["routine"], "U_pred": round(out["U"], 4),
            "urgent_hit_pred": round(out["urgent_hit"], 4), "routine_hit_pred": round(out["routine_hit"], 4)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", required=True)
    a = ap.parse_args()
    runs = yaml.safe_load(open(os.path.join(ROOT, "experiments/matrices/board-grid.yaml")))["runs"]
    with mp.Pool(7) as pool:
        rows = pool.map(predict, runs)
    with open(a.o, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print(f"wrote {a.o}: {len(rows)} predictions")


if __name__ == "__main__":
    main()
