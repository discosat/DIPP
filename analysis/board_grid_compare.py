#!/usr/bin/env python3
"""Board grid against its predictions and the static policies, per cell.

    python3 analysis/parse_runs.py runs/board-grid/<runs> -o analysis/parsed/board-grid
    python3 analysis/campaign_utility.py analysis/parsed/board-grid -o analysis/board-grid
    python3 analysis/board_grid_compare.py analysis/board-grid -o analysis/board-grid

Inputs (all explicit; a missing file is an error):
  <dir>/runs.csv                         campaign_utility output for the grid runs
  --predictions CSV                      simulated predictions (predict_board_grid.py)
  analysis/eval_final/runs.csv           P2 comparison runs (static low/med/high,
                                         deadline-only "greedy", earlier final "adaptive"):
                                         the grid does not repeat them at P2
  runs/board-grid/<label>/RUN.json and runs/eval_final/<label>/RUN.json   Otii energy

Writes compare_runs.csv (one row per run: board U, predicted U, energy) and
compare.md (per cell: every policy's seed-1 U, the final policy's seed range,
regret against the best static, energy).
"""
import argparse, csv, json, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = ("low", "med", "high")
P2_FROM_EVAL = {"low": "low", "med": "med", "high": "high", "greedy": "greedy"}
TIE = 0.02  # METRIC.md


def read(path):
    if not os.path.exists(path):
        raise SystemExit(f"missing input: {path}")
    return list(csv.DictReader(open(path)))


def energy(run_dir):
    p = os.path.join(run_dir, "RUN.json")
    e = json.load(open(p)).get("energy") or {}
    if "energy_wh" not in e:
        raise SystemExit(f"no Otii energy in {p}")
    return e


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("-o", required=True)
    ap.add_argument("--predictions", required=True,
                    help="analysis/board_grid_predictions_fixed.csv (the original file double-scaled "
                         "P1/P3/P4 routine deadlines; METRIC.md addendum 2026-10-16)")
    a = ap.parse_args()
    pred = {r["label"]: r for r in read(os.path.join(ROOT, a.predictions))}
    rows = []
    for r in read(os.path.join(a.dir, "runs.csv")):
        p = pred.get(r["run"])
        if p is None:
            raise SystemExit(f"no prediction for {r['run']}")
        e = energy(os.path.join(ROOT, "runs/board-grid", r["run"]))
        rows.append({"run": r["run"], "cell": r["cell"], "policy": r["policy"], "seed": int(r["seed"]),
                     "source": "board-grid", "U": float(r["U"]), "U_pred": float(p["U_pred"]),
                     "urgent_hit": float(r["urgent_hit"]), "routine_hit": float(r["routine_hit"]),
                     "late": int(r["late"]), "dropped": int(r["dropped"]),
                     "energy_wh": e["energy_wh"], "avg_power_w": e["avg_power_w"]})
    # P2 cells: statics and deadline-only come from eval_final (same traces, content, deadlines).
    p2cells = {r["cell"][2:] for r in rows if r["cell"].startswith("P2")}
    for r in read(os.path.join(ROOT, "analysis/eval_final/runs.csv")):
        if r["cell"] in p2cells and r["policy"] in P2_FROM_EVAL:
            e = energy(os.path.join(ROOT, "runs/eval_final", r["run"]))
            rows.append({"run": "eval_final/" + r["run"], "cell": "P2" + r["cell"],
                         "policy": P2_FROM_EVAL[r["policy"]], "seed": int(r["seed"]), "source": "eval_final",
                         "U": float(r["U"]), "U_pred": "", "urgent_hit": float(r["urgent_hit"]),
                         "routine_hit": float(r["routine_hit"]), "late": int(r["late"]),
                         "dropped": int(r["dropped"]), "energy_wh": e["energy_wh"],
                         "avg_power_w": e["avg_power_w"]})
    os.makedirs(a.o, exist_ok=True)
    with open(os.path.join(a.o, "compare_runs.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: (x["cell"], x["policy"], x["seed"])))

    order = ["low", "med", "high", "greedy", "final"]
    cells = sorted({r["cell"] for r in rows}, key=lambda c: (c.startswith("E37"), c))
    out = ["# Board grid: comparison (seed 1 unless stated)", "",
           "U = utility within deadline; pred = simulated before the run; regret = best static U - final U "
           f"(tie if |regret| < {TIE}); Wh = Otii whole-board energy over the run.", "",
           "| cell | " + " | ".join(order) + " | best static | final regret | final seeds U | final pred | "
           "final urgent hit (min) | final Wh vs best static Wh |",
           "|---" * (len(order) + 6) + "|"]
    worst = None
    for c in cells:
        s1 = {r["policy"]: r for r in rows if r["cell"] == c and r["seed"] == 1}
        fin = [r for r in rows if r["cell"] == c and r["policy"] == "final"]
        stat = {p: s1[p]["U"] for p in STATIC if p in s1}
        best = max(stat, key=stat.get) if stat else None
        reg = round(stat[best] - s1["final"]["U"], 4) if best and "final" in s1 else None
        if reg is not None and (worst is None or reg > worst[1]):
            worst = (c, reg)
        us = [r["U"] for r in fin]
        cellv = lambda p: f"{s1[p]['U']:.3f}" if p in s1 else "-"
        wh = (f"{s1['final']['energy_wh']:.2f} vs {s1[best]['energy_wh']:.2f}"
              if best and "final" in s1 else "-")
        out.append(f"| {c} | " + " | ".join(cellv(p) for p in order) +
                   f" | {best or '-'} | {reg if reg is not None else '-'}"
                   f"{' (tie)' if reg is not None and abs(reg) < TIE else ''}"
                   f" | {min(us):.3f}-{max(us):.3f} (n={len(us)}) | "
                   f"{s1['final']['U_pred'] if 'final' in s1 else '-'} | "
                   f"{min(r['urgent_hit'] for r in fin):.2f} | {wh} |")
    out += ["", f"Worst final regret (seed 1): {worst[1]} in {worst[0]}" if worst else "No static comparison yet.", "",
            "## Board vs prediction (every grid run)", "",
            "| run | U board | U pred | diff |", "|---|---|---|---|"]
    diffs = []
    for r in sorted((r for r in rows if r["source"] == "board-grid"), key=lambda x: x["run"]):
        d = r["U"] - r["U_pred"]
        diffs.append((abs(d), r["run"]))
        out.append(f"| {r['run']} | {r['U']:.4f} | {r['U_pred']:.4f} | {d:+.4f} |")
    out += ["", f"Largest |board - pred|: {max(diffs)[0]:.4f} ({max(diffs)[1]}); "
            f"runs within 0.03: {sum(d < 0.03 for d, _ in diffs)}/{len(diffs)}"]
    open(os.path.join(a.o, "compare.md"), "w").write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
