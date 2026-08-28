#!/usr/bin/env python3
"""CSV -> LaTeX tables and figures.

Consumes what `parse_runs.py` emits (`executions.csv`, `runs.csv`) and produces
the two worked artifacts Stage 4 owes: the Table VI shape (per-module
persistence overhead, MEM vs MMAP) and the Figs 8/9 replacement (SoC, queue
depth and chosen effort against wall-clock time).

    analysis/parse_runs.py runs/<set>/*  -o analysis/parsed
    analysis/tables.py --parsed analysis/parsed --out analysis/stage4

Two design choices are experimental, not cosmetic, and both come from the plan:

  * The first batch of every run is discarded. Batch 1 inflates high-effort
    classification +110% and JXL +98% -- it is measuring first-touch, not
    steady state -- so a table built without the discard reports cold-start
    cost under a steady-state caption.
  * Dispersion is median + IQR over *independent process launches*, and the
    MMAP-minus-MEM difference carries a bootstrap CI. Where that CI spans zero
    the cell is marked n.s. rather than given a spuriously precise percentage:
    "indistinguishable from zero" is a stronger and less attackable claim than
    the original 0.24%.
"""

import argparse
import collections
import csv
import glob
import json
import os
import re
import sys

import numpy as np

EFFORT_ORDER = {"low": 0, "medium": 1, "high": 2, "default": 3}
BOOTSTRAP = 2000
MIN_LAUNCHES = 5  # n>=5 independent process launches per cell
SEED = 43
BATTERY_CAPACITY_WH = 92.0  # SI-DISCOII-DR-001


# ---------------------------------------------------------------- config ----

def load_effort_map(config_dir):
    """daemon param_id -> (stage_name, effort_level, stage_order, config_slot).

    The daemon's `param_id` is a zero-based index into `module_parameter_lists`,
    while the flight config numbers its slots from one to match the CSP params
    `module_param_1..12`. So config slot N is daemon index N-1 -- verified
    against latency hints: slots 1/2/3/6/11 (373000, 143000, 235000, 120000,
    149000 us) are exactly what the daemon reported at indices 0/1/2/5/10.
    Getting this off by one silently mislabels every effort level.

    Effort labels are read from the flight config rather than hardcoded, but
    only a pipeline that offers more than one implementation per stage actually
    names them: pipeline 2 calls param 3 "default" where pipeline 1 calls the
    same param "low". So multi-implementation pipelines win, and single-
    implementation ones only fill gaps.
    """
    if not config_dir or not os.path.isdir(config_dir):
        return {}
    try:
        import yaml
    except ImportError:
        print("note: PyYAML unavailable; effort labels fall back to param id",
              file=sys.stderr)
        return {}

    strong, weak = {}, {}
    for path in sorted(glob.glob(os.path.join(config_dir, "pipeline_*.yaml"))):
        doc = yaml.safe_load(open(path)) or {}
        for order, stage in enumerate(doc.get("pipeline", [])):
            impls = stage.get("implementations", []) or []
            target = strong if len(impls) > 1 else weak
            for impl in impls:
                pid = impl.get("param_id")
                if pid is None:
                    continue
                target.setdefault(pid - 1, (stage.get("name", "?"),
                                            str(impl.get("effort_level", "default")),
                                            order, pid))
    merged = dict(weak)
    merged.update(strong)
    return merged


# ------------------------------------------------------------------ load ----

def read_csv(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def num(row, key, default=None):
    v = row.get(key)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return default


def load(parsed_dir):
    execs = read_csv(os.path.join(parsed_dir, "executions.csv"))
    runs = {r["run_id"]: r for r in read_csv(os.path.join(parsed_dir, "runs.csv"))}
    if not execs:
        sys.exit(f"no executions in {parsed_dir}/executions.csv -- run parse_runs.py first")
    return execs, runs


def drop_warmup(execs):
    """Discard every execution belonging to each run's first batch."""
    best = {}  # run_id -> (sort_key, uuid); ts_us when present, file order otherwise
    for i, e in enumerate(execs):
        rid, uuid = e.get("run_id"), e.get("uuid")
        if not uuid:
            continue
        ts = num(e, "ts_us")
        key = (0, ts) if ts is not None else (1, i)
        if rid not in best or key < best[rid][0]:
            best[rid] = (key, uuid)
    warm = {rid: v[1] for rid, v in best.items()}
    kept = [e for e in execs if e.get("uuid") != warm.get(e.get("run_id"))]
    return kept, len(execs) - len(kept)


# -------------------------------------------------------------- stats ------

def median_iqr(xs):
    a = np.asarray(xs, dtype=float)
    return float(np.median(a)), float(np.percentile(a, 25)), float(np.percentile(a, 75))


def bootstrap_median_diff(a, b, rng):
    """95% CI of median(b) - median(a). Returns (lo, hi)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return None
    d = np.empty(BOOTSTRAP)
    for i in range(BOOTSTRAP):
        d[i] = (np.median(rng.choice(b, len(b), replace=True))
                - np.median(rng.choice(a, len(a), replace=True)))
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


# ------------------------------------------------------------- table VI ----

def table6(execs, runs, effort_map):
    """Per (stage, effort) latency, MEM vs MMAP, with the overhead between."""
    rng = np.random.default_rng(SEED)
    cells = collections.defaultdict(lambda: collections.defaultdict(list))
    launches = collections.defaultdict(lambda: collections.defaultdict(set))

    for e in execs:
        pid = num(e, "param_id")
        lat = num(e, "latency_meas_us")
        if pid is None or lat is None:
            continue
        run = runs.get(e.get("run_id"), {})
        mode = (run.get("storage") or "?").upper()
        stage, effort, order, slot = effort_map.get(
            pid, (e.get("module") or "?", "default", 99, None))
        key = (order, stage, effort, pid, slot)
        cells[key][mode].append(lat / 1000.0)  # ms
        launches[key][mode].add(e.get("run_id"))

    rows = []
    for key in sorted(cells, key=lambda k: (k[0], EFFORT_ORDER.get(k[2], 9), k[3])):
        _order, stage, effort, pid, slot = key
        by_mode = cells[key]
        row = {"stage": stage, "effort": effort, "param_id": pid,
               "config_slot": slot}
        for mode in ("MEM", "MMAP"):
            xs = by_mode.get(mode, [])
            if xs:
                med, q1, q3 = median_iqr(xs)
                row[f"{mode}_median_ms"] = med
                row[f"{mode}_iqr_ms"] = q3 - q1
                row[f"{mode}_n_exec"] = len(xs)
                row[f"{mode}_n_runs"] = len(launches[key][mode])
        mem, mmap_ = by_mode.get("MEM", []), by_mode.get("MMAP", [])
        if mem and mmap_:
            ci = bootstrap_median_diff(mem, mmap_, rng)
            row["overhead_pct"] = 100.0 * (np.median(mmap_) - np.median(mem)) / np.median(mem)
            if ci:
                row["ci_lo_ms"], row["ci_hi_ms"] = ci
                row["significant"] = not (ci[0] <= 0.0 <= ci[1])
        rows.append(row)
    return rows


def fmt(v, prec=1):
    return "--" if v is None else f"{v:,.{prec}f}"


def table6_latex(rows, caption_n):
    out = [
        r"% generated by analysis/tables.py -- do not edit by hand",
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Per-module execution latency by storage backend. Median and "
        r"interquartile range over independent process launches, first batch of "
        r"each run discarded. Overhead is the median MMAP$-$MEM difference; "
        r"\emph{n.s.} marks cells whose 95\% bootstrap confidence interval "
        r"includes zero, i.e.\ persistence overhead is not distinguishable from "
        rf"zero. {caption_n}}}",
        r"\label{tab:overhead}",
        r"\begin{tabular}{llrrrrr}",
        r"\toprule",
        r"Stage & Effort & \multicolumn{2}{c}{MEM (ms)} & "
        r"\multicolumn{2}{c}{MMAP (ms)} & Overhead \\",
        r"\cmidrule(lr){3-4}\cmidrule(lr){5-6}",
        r" & & median & IQR & median & IQR & \\",
        r"\midrule",
    ]
    for r in rows:
        stage = r["stage"].replace("lib", "").replace("_", r"\_")
        if "overhead_pct" not in r:
            over = "--"
        elif r.get("significant"):
            over = f"{r['overhead_pct']:+.1f}\\%"
        else:
            over = r"n.s."
        out.append(
            f"{stage} & {r['effort']} & "
            f"{fmt(r.get('MEM_median_ms'))} & {fmt(r.get('MEM_iqr_ms'))} & "
            f"{fmt(r.get('MMAP_median_ms'))} & {fmt(r.get('MMAP_iqr_ms'))} & "
            f"{over} \\\\")
    out += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(out) + "\n"


def table6_markdown(rows):
    out = ["| Stage | Effort | slot | MEM median ms | MEM IQR | n (exec/runs) | "
           "MMAP median ms | MMAP IQR | n (exec/runs) | Overhead |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if "overhead_pct" not in r:
            over = "--"
        elif r.get("significant"):
            over = (f"{r['overhead_pct']:+.1f}% "
                    f"(CI {r['ci_lo_ms']:+.2f}..{r['ci_hi_ms']:+.2f} ms)")
        else:
            over = f"n.s. (CI {r['ci_lo_ms']:+.2f}..{r['ci_hi_ms']:+.2f} ms)"
        out.append(
            f"| {r['stage']} | {r['effort']} | {r['config_slot'] or '?'} | "
            f"{fmt(r.get('MEM_median_ms'))} | {fmt(r.get('MEM_iqr_ms'))} | "
            f"{r.get('MEM_n_exec', 0)}/{r.get('MEM_n_runs', 0)} | "
            f"{fmt(r.get('MMAP_median_ms'))} | {fmt(r.get('MMAP_iqr_ms'))} | "
            f"{r.get('MMAP_n_exec', 0)}/{r.get('MMAP_n_runs', 0)} | {over} |")
    return "\n".join(out) + "\n"


# -------------------------------------------------------------- figure -----

def timeline(execs, runs, effort_map, run_id, path):
    """Figs 8/9 replacement: SoC, queue depth and chosen effort against time."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = [e for e in execs if e.get("run_id") == run_id and num(e, "ts_us") is not None]
    if not rows:
        return None
    rows.sort(key=lambda e: num(e, "ts_us"))
    t0 = num(rows[0], "ts_us")
    t = np.array([(num(e, "ts_us") - t0) / 1e6 for e in rows])

    soc = np.array([100.0 * (num(e, "battery_wh", 0.0) or 0.0) / BATTERY_CAPACITY_WH
                    for e in rows])
    ingest = np.array([num(e, "ingest_depth", 0) or 0 for e in rows])
    partial = np.array([num(e, "partial_depth", 0) or 0 for e in rows])
    eff = np.array([EFFORT_ORDER.get(
        effort_map.get(num(e, "param_id"), ("", "default", 0, None))[1], 3)
        for e in rows])

    fig, ax = plt.subplots(3, 1, sharex=True, figsize=(7, 6.2))
    ax[0].plot(t, soc, lw=1.2, color="#1f77b4")
    ax[0].set_ylabel("Battery SoC (\\%)" if plt.rcParams["text.usetex"] else "Battery SoC (%)")
    ax[0].grid(alpha=.3)

    ax[1].step(t, ingest, where="post", lw=1.1, label="ingest")
    ax[1].step(t, partial, where="post", lw=1.1, label="partially processed")
    ax[1].set_ylabel("Queue depth")
    ax[1].legend(loc="upper right", fontsize=8)
    ax[1].grid(alpha=.3)

    ax[2].scatter(t, eff, s=12, color="#2ca02c")
    ax[2].set_yticks(list(EFFORT_ORDER.values()))
    ax[2].set_yticklabels(list(EFFORT_ORDER))
    ax[2].set_ylabel("Effort")
    ax[2].set_xlabel("Time since first execution (s)")
    ax[2].grid(alpha=.3)

    run = runs.get(run_id, {})
    fig.suptitle(f"{run_id}  (pipeline {run.get('pipeline', '?')}, "
                 f"{run.get('storage', '?')}, {run.get('heuristic', '?')})",
                 fontsize=10)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(f"{path}.{ext}", dpi=150)
    plt.close(fig)
    return f"{path}.pdf"


# ---------------------------------------------------------------- main -----

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed", default="analysis/parsed")
    ap.add_argument("--out", default="analysis/stage4")
    ap.add_argument("--config", default="experiments/board/flight-config")
    ap.add_argument("--timeline-run", help="run_id for the Figs 8/9 timeline "
                                           "(default: the run with most executions)")
    ap.add_argument("--no-warmup-discard", action="store_true")
    a = ap.parse_args()

    execs, runs = load(a.parsed)
    effort_map = load_effort_map(a.config)

    if a.no_warmup_discard:
        dropped = 0
    else:
        execs, dropped = drop_warmup(execs)
    os.makedirs(a.out, exist_ok=True)

    modes = collections.Counter((runs.get(e["run_id"], {}).get("storage") or "?").upper()
                                for e in execs)
    n_runs = len({e["run_id"] for e in execs})
    caption_n = (f"$n={n_runs}$ runs, {len(execs)} executions "
                 f"({dropped} warm-up executions discarded).")
    print(f"{len(execs)} executions across {n_runs} runs "
          f"({dropped} warm-up discarded); storage modes: {dict(modes)}")
    if "?" in modes:
        print("warning: some runs have no storage mode in runs.csv -- "
              "MEM/MMAP columns will be incomplete", file=sys.stderr)

    rows = table6(execs, runs, effort_map)

    # Dispersion is only meaningful across independent process launches. A cell
    # backed by one launch measures within-process repetition, which is a much
    # smaller number than run-to-run variation and would understate every
    # interval in the table. Say so on the artifact itself rather than trusting
    # whoever reads it to remember.
    launch_counts = [r.get(f"{m}_n_runs", 0) for r in rows for m in ("MEM", "MMAP")
                     if r.get(f"{m}_n_exec")]
    min_launches = min(launch_counts) if launch_counts else 0
    if min_launches < MIN_LAUNCHES:
        caption_n += (f" \\emph{{Preliminary: {min_launches} independent launch(es) "
                      f"in the smallest cell, fewer than the {MIN_LAUNCHES} this "
                      f"table requires; intervals are within-run repetition.}}")
        print(f"WARNING: smallest cell has {min_launches} independent launch(es), "
              f"want >= {MIN_LAUNCHES}. Table is marked preliminary.", file=sys.stderr)
    open(os.path.join(a.out, "table6_overhead.tex"), "w").write(
        table6_latex(rows, caption_n))
    open(os.path.join(a.out, "table6_overhead.md"), "w").write(table6_markdown(rows))
    json.dump(rows, open(os.path.join(a.out, "table6_overhead.json"), "w"),
              indent=2, default=float)

    run_id = a.timeline_run
    if not run_id:
        c = collections.Counter(e["run_id"] for e in execs)
        run_id = c.most_common(1)[0][0]
    fig = timeline(execs, runs, effort_map, run_id, os.path.join(a.out, "fig_timeline"))

    print(f"wrote {a.out}/table6_overhead.{{tex,md,json}} ({len(rows)} rows)")
    print(f"wrote {fig}" if fig else
          "timeline skipped: no ts_us in executions.csv (redeploy the daemon)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
