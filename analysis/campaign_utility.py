#!/usr/bin/env python3
"""Utility within deadline, its decomposition, and regret, from parsed
campaign runs -- the quantities analysis/stage5/METRIC.md fixes.

    python3 analysis/parse_runs.py runs/eval/* -o analysis/parsed/eval
    python3 analysis/campaign_utility.py analysis/parsed/eval -o analysis/eval

Run labels are <cell>-<policy>-s<seed> (experiments/matrices/gen_eval.py).
Per frame: q = mean over classification, segmentation, encoder of
quality(effort used) / quality(high), from the EXEC records' slots. Per run:
U (and the classification-only / encoder-only variants), hit rate, mean q of
on-time frames, late / dropped, latency p50/p95/p99/max and blocking per
class, tiles and bytes delivered on time, preemptions. Per cell: every policy
on seed 1 (the paired comparison, same trace and content), the mean over the
seeds that exist, and regret against the best static policy in hindsight.
Writes runs.csv, cells.csv and a markdown summary to the output directory.
"""
import argparse, csv, os, re, statistics, sys
from collections import defaultdict

# slot (= EXEC param_id + 1) -> (stage, effort)
SLOT = {3: ("cls", "low"), 4: ("cls", "med"), 5: ("cls", "high"),
        6: ("seg", "low"), 19: ("seg", "med"), 7: ("seg", "high"),
        16: ("enc", "low"), 17: ("enc", "med"), 18: ("enc", "high")}
QUAL = {"cls": {"low": .763, "med": .935, "high": 1.0},          # METRIC.md
        "seg": {"low": .752, "med": .960, "high": 1.0},
        "enc": {"low": .858, "med": .951, "high": 1.0}}
STATIC = ("low", "med", "high")
LABEL = re.compile(r"^(C\d+)-(.+)-s(\d+)$")


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))] if v else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("parsed_dir")
    ap.add_argument("-o", "--outdir", required=True)
    a = ap.parse_args()
    eff = defaultdict(dict)                       # (run, uuid) -> {stage: effort}
    for e in csv.DictReader(open(os.path.join(a.parsed_dir, "executions.csv"))):
        st = SLOT.get(int(e["param_id"]) + 1)
        if st:
            eff[(e["run_id"], e["uuid"])][st[0]] = st[1]
    batches = defaultdict(list)
    for b in csv.DictReader(open(os.path.join(a.parsed_dir, "batches.csv"))):
        batches[b["run_id"]].append(b)
    runmeta = {r["run_id"]: r for r in csv.DictReader(open(os.path.join(a.parsed_dir, "runs.csv")))}

    rows = []
    for run, bs in sorted(batches.items()):
        m = LABEL.match(run)
        if not m:
            continue
        cell, policy, seed = m.group(1), m.group(2), int(m.group(3))
        N = len(bs)
        U = Uc = Ue = 0.0
        on_q, tiles, nbytes = [], 0, 0
        by = {"urgent": [], "routine": []}
        incomplete_effort = 0
        for b in bs:
            k = (run, b["uuid"])
            met = b.get("deadline_met") == "1"
            by.setdefault(b.get("cls") or "unknown", []).append(b)
            if not met:
                continue
            e = eff.get(k, {})
            if len(e) < 3:
                incomplete_effort += 1
                continue
            q = statistics.mean(QUAL[s][e[s]] for s in ("cls", "seg", "enc"))
            U += q; Uc += QUAL["cls"][e["cls"]]; Ue += QUAL["enc"][e["enc"]]
            on_q.append(q)
            tiles += int(b["tiles_delivered"] or 0); nbytes += int(b["downlink_bytes"] or 0)
        row = {"run": run, "cell": cell, "policy": policy, "seed": seed, "frames": N,
               "U": round(U / N, 4), "U_cls_only": round(Uc / N, 4), "U_enc_only": round(Ue / N, 4),
               "q_on_time": round(statistics.mean(on_q), 4) if on_q else None,
               "late": sum(1 for b in bs if b.get("completed") == "1" and b.get("deadline_met") != "1"),
               "dropped": sum(1 for b in bs if b.get("dropped") == "1"),
               "unfinished": sum(1 for b in bs if b.get("completed") != "1" and b.get("dropped") != "1"),
               "tiles_on_time": tiles, "bytes_on_time": nbytes,
               "preemptions": runmeta.get(run, {}).get("preemptions"),
               "missing_effort_records": incomplete_effort}
        for cls, cb in by.items():
            if cls not in ("urgent", "routine") or not cb:
                continue
            lat = [int(b["latency_us"]) / 1e6 for b in cb if b.get("latency_us")]
            row[f"{cls}_n"] = len(cb)
            row[f"{cls}_hit"] = round(sum(1 for b in cb if b.get("deadline_met") == "1") / len(cb), 4)
            for p, nm in ((.5, "p50"), (.95, "p95"), (.99, "p99"), (1.0, "max")):
                row[f"{cls}_{nm}_s"] = round(pct(lat, p), 2) if lat else None
            row[f"{cls}_blocking_max_s"] = round(max(int(b["blocking_us"]) for b in cb) / 1e6, 2)
        rows.append(row)
    if not rows:
        raise SystemExit(f"no <cell>-<policy>-s<seed> runs in {a.parsed_dir}")

    # cells: seed-1 pairing + seed means, regret vs best static
    cells = []
    by_cp = defaultdict(dict)
    for r in rows:
        by_cp[(r["cell"], r["policy"])][r["seed"]] = r
    for c in sorted({r["cell"] for r in rows}):
        pols = sorted(p for (cc, p) in by_cp if cc == c)
        s1 = {p: by_cp[(c, p)][1]["U"] for p in pols if 1 in by_cp[(c, p)]}
        statics = {p: s1[p] for p in STATIC if p in s1}
        best_p = max(statics, key=statics.get) if statics else None
        for p in pols:
            seeds = by_cp[(c, p)]
            us = [seeds[s]["U"] for s in sorted(seeds)]
            cells.append({"cell": c, "policy": p, "U_s1": s1.get(p), "U_mean": round(statistics.mean(us), 4),
                          "U_min": min(us), "U_max": max(us), "seeds": len(us),
                          "best_static": best_p, "regret_s1": (round(statics[best_p] - s1[p], 4)
                                                               if best_p and p in s1 else None),
                          "urgent_hit_s1": seeds.get(1, {}).get("urgent_hit"),
                          "routine_hit_s1": seeds.get(1, {}).get("routine_hit"),
                          "urgent_p99_s1": seeds.get(1, {}).get("urgent_p99_s")})
    os.makedirs(a.outdir, exist_ok=True)
    for name, data in (("runs.csv", rows), ("cells.csv", cells)):
        cols = []
        for r in data:
            cols += [k for k in r if k not in cols]
        with open(os.path.join(a.outdir, name), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader(); w.writerows(data)
    worst = defaultdict(float)
    for r in cells:
        if r["regret_s1"] is not None:
            worst[r["policy"]] = max(worst[r["policy"]], r["regret_s1"])
    lines = ["| cell | policy | U (s1) | U mean [min-max], seeds | regret vs best static | urgent hit | routine hit | urgent p99 s |",
             "|---|---|---|---|---|---|---|---|"]
    for r in cells:
        lines.append(f"| {r['cell']} | {r['policy']} | {r['U_s1']} | {r['U_mean']} [{r['U_min']}-{r['U_max']}], {r['seeds']} "
                     f"| {r['regret_s1']} | {r['urgent_hit_s1']} | {r['routine_hit_s1']} | {r['urgent_p99_s1']} |")
    lines += ["", "Worst-case regret (seed 1, cells present): " +
              ", ".join(f"{p} {v:.3f}" for p, v in sorted(worst.items(), key=lambda x: x[1]))]
    open(os.path.join(a.outdir, "summary.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
