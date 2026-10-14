#!/usr/bin/env python3
"""Aggregate parsed campaign runs into a policy table (Table VII and the arms).

    python3 analysis/parse_runs.py runs/table7-core/* -o analysis/parsed/table7-core
    python3 analysis/campaign_table.py analysis/parsed/table7-core -o analysis/table7-core.csv

Groups runs by label with the trailing replicate (-s<seed>) removed, e.g.
t7-adaptive-s1..s3 -> t7-adaptive. Per group: number of runs, and the mean
and the min-max over replicates of each metric. Energy is the payload's:
the sum of energy debited (the simulated battery's view) and the Otii's
whole-board energy above the idle floor is NOT used here -- campaign runs
reboot per run and carry no floor; it is in RUN.json for reference.
"""
import argparse, csv, os, re, statistics, sys
from collections import defaultdict

METRICS = [
    ("urgent_met_frac", "urgent met"), ("routine_met_frac", "routine met"),
    ("urgent_latency_p95_s", "urgent p95 s"), ("routine_latency_p95_s", "routine p95 s"),
    ("urgent_blocking_max_s", "urgent blocking max s"),
    ("batches_unfinished", "unfinished"), ("batches_dropped", "dropped"),
    ("halts", "halts"), ("halted_s_total", "halted s"),
    ("battery_wh_min", "battery min Wh"), ("battery_empty", "battery empty"),
    ("energy_debited_wh", "payload energy Wh"), ("board_wh_last", "board idle Wh"),
    ("cost_src_hint", "hint predictions"),
]


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("parsed_dir")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    runs = list(csv.DictReader(open(os.path.join(a.parsed_dir, "runs.csv"))))
    bat = defaultdict(float)
    bp = os.path.join(a.parsed_dir, "batches.csv")
    if os.path.exists(bp):
        for b in csv.DictReader(open(bp)):
            bat[b["run_id"]] += (f(b.get("energy_debited_uwh")) or 0.0) / 1e6
    groups = defaultdict(list)
    for r in runs:
        r["energy_debited_wh"] = round(bat.get(r["run_id"], 0.0), 4)
        groups[re.sub(r"-s\d+$", "", r["run_id"])].append(r)
    out = []
    for g, rs in sorted(groups.items()):
        row = {"group": g, "runs": len(rs)}
        for k, _ in METRICS:
            v = [f(r.get(k)) for r in rs if f(r.get(k)) is not None]
            row[f"{k}_mean"] = round(statistics.mean(v), 4) if v else None
            row[f"{k}_min"] = min(v) if v else None
            row[f"{k}_max"] = max(v) if v else None
        out.append(row)
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader(); w.writerows(out)
    keys = ["urgent_met_frac", "routine_met_frac", "urgent_latency_p95_s", "batches_unfinished",
            "halted_s_total", "energy_debited_wh", "battery_wh_min"]
    print(f"{'group':28} {'n':>2} " + " ".join(f"{k[:14]:>14}" for k in keys))
    for r in out:
        print(f"{r['group']:28} {r['runs']:>2} " + " ".join(
            f"{'' if r[k + '_mean'] is None else round(r[k + '_mean'], 3):>14}" for k in keys))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
