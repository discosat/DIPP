#!/usr/bin/env python3
"""analysis/campaign_utility.py on synthetic parsed runs.   python3 tests/test_campaign_utility.py"""
import csv, os, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails = 0


def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  ({detail})"))
    fails += 0 if ok else 1


def write(path, rows):
    cols = []
    for r in rows:
        cols += [k for k in r if k not in cols]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader(); w.writerows(rows)


# Two frames per run. Slots (param_id = slot - 1): cls 3/4/5, seg 6/19/7, enc 16/17/18.
EFF = {"low": (3, 6, 16), "med": (4, 19, 17), "high": (5, 7, 18)}
runs = {  # run -> list of (effort, met, completed, cls, latency_s)
    "C1-high-s1": [("high", 1, 1, "urgent", 5), ("high", 0, 1, "routine", 400)],   # one late
    "C1-med-s1": [("med", 1, 1, "urgent", 4), ("med", 1, 1, "routine", 9)],
    "C1-low-s1": [("low", 1, 1, "urgent", 2), ("low", 1, 1, "routine", 3)],
    "C1-adaptive-s1": [("high", 1, 1, "urgent", 6), ("med", 1, 1, "routine", 8)],
    "C1-adaptive-s2": [("high", 1, 1, "urgent", 6), ("high", 1, 1, "routine", 8)],
}
ex, bt = [], []
for run, frames in runs.items():
    for i, (e, met, done, cls, lat) in enumerate(frames):
        u = f"{run}-{i}"
        for slot in EFF[e]:
            ex.append({"run_id": run, "uuid": u, "param_id": slot - 1, "module": "m"})
        bt.append({"run_id": run, "uuid": u, "cls": cls, "deadline_met": met, "completed": done, "dropped": 0,
                   "latency_us": lat * 1_000_000, "blocking_us": 0, "tiles_delivered": 10, "downlink_bytes": 1000})
with tempfile.TemporaryDirectory() as d:
    write(os.path.join(d, "executions.csv"), ex)
    write(os.path.join(d, "batches.csv"), bt)
    write(os.path.join(d, "runs.csv"), [{"run_id": r, "preemptions": 0} for r in runs])
    out = os.path.join(d, "out")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "analysis/campaign_utility.py"), d, "-o", out],
                       capture_output=True, text=True)
    check("runs", r.returncode == 0, r.stderr)
    R = {x["run"]: x for x in csv.DictReader(open(os.path.join(out, "runs.csv")))}
    C = {(x["cell"], x["policy"]): x for x in csv.DictReader(open(os.path.join(out, "cells.csv")))}

q_med = (.935 + .960 + .951) / 3
check("high: one late frame counts 0 -> U = 1/2", abs(float(R["C1-high-s1"]["U"]) - 0.5) < 1e-4, R["C1-high-s1"]["U"])
check("med: U = q_med", abs(float(R["C1-med-s1"]["U"]) - q_med) < 1e-3, R["C1-med-s1"]["U"])
check("adaptive s1: U = (1 + q_med) / 2", abs(float(R["C1-adaptive-s1"]["U"]) - (1 + q_med) / 2) < 1e-3)
check("cls-only variant uses classification quality alone",
      abs(float(R["C1-med-s1"]["U_cls_only"]) - .935) < 1e-4, R["C1-med-s1"]["U_cls_only"])
check("best static in C1 is med", C[("C1", "adaptive")]["best_static"] == "med")
check("regret of adaptive on seed 1 is negative (beats best static)",
      float(C[("C1", "adaptive")]["regret_s1"]) < 0, C[("C1", "adaptive")]["regret_s1"])
check("regret of med is 0", abs(float(C[("C1", "med")]["regret_s1"])) < 1e-9)
check("adaptive aggregates 2 seeds", C[("C1", "adaptive")]["seeds"] == "2")
check("tiles counted only for on-time frames", R["C1-high-s1"]["tiles_on_time"] == "10")
check("urgent hit rate per class", R["C1-high-s1"]["urgent_hit"] == "1.0" and R["C1-high-s1"]["routine_hit"] == "0.0")
print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
