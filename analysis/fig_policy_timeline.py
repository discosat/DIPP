#!/usr/bin/env python3
"""Logged timelines of two or more runs side by side, to show what the policy
did: every frame's latency against its deadline, the ingestion queue depth
with the policy's depth thresholds and the periods the overload guard held
lowest effort, and the effort the modules ran at.

    python3 analysis/fig_policy_timeline.py PARSED_DIR RUN:TITLE [RUN:TITLE ...] -o fig/name

PARSED_DIR is analysis/parse_runs.py output (batches.csv, executions.csv,
events.csv). The x axis is the board's processing clock (minutes since the
run's first ingest); gaps between bursts are fast-forwarded and therefore
short. Writes NAME.pdf and NAME.png.
"""
import argparse, csv, os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
ROUTINE, URGENT = "#2a78d6", "#eb6834"
SHADE = "#ecebe6"
EFFORT_COL = {"low": "#c9d9ee", "med": "#6f9fd8", "high": "#1d4f91"}
# slot (= EXEC param_id + 1) -> effort; as campaign_utility.SLOT
SLOT_EFFORT = {3: "low", 4: "med", 5: "high", 6: "low", 19: "med", 7: "high",
               16: "low", 17: "med", 18: "high"}
BIN_S = 120


def load(path, run):
    return [r for r in csv.DictReader(open(path)) if r["run_id"] == run]


def style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=INK2, labelsize=7)
    ax.grid(axis="y", color=GRID, linewidth=0.5)
    ax.set_axisbelow(True)


def panel(axes, pdir, run, title, show_ylabels):
    bts = load(os.path.join(pdir, "batches.csv"), run)
    exs = load(os.path.join(pdir, "executions.csv"), run)
    evs = [e for e in load(os.path.join(pdir, "events.csv"), run) if e["tag"] == "SCHED_TIER"]
    meta = load(os.path.join(pdir, "runs.csv"), run)
    if not meta or not meta[0].get("pipeline") or not meta[0].get("sched_guard"):
        raise SystemExit(f"{run}: runs.csv lacks pipeline / sched_guard")
    # Only the adaptive pipeline (1) consults the tiers; static pipelines log
    # them but run one effort, so thresholds and guard are not drawn for them.
    adaptive = meta[0]["pipeline"] == "1"
    guard_on = adaptive and meta[0]["sched_guard"] == "on"
    if not bts or not exs:
        raise SystemExit(f"{run}: no batches or executions in {pdir}")
    t0 = min(int(b["ingest_ts_us"]) for b in bts if b["ingest_ts_us"])
    m = lambda us: (int(us) - t0) / 60e6
    t_end = max(m(e["ts_us"]) for e in exs)

    # (a) latency vs deadline at capture time
    ax = axes[0]
    style(ax)
    top = 1500
    for cls, col in (("routine", ROUTINE), ("urgent", URGENT)):
        xs, ys, xm = [], [], []
        for b in bts:
            if b["cls"] != cls:
                continue
            x = m(b["arrival_ts_us"] or b["ingest_ts_us"])
            if b["completed"] == "1":
                xs.append(x); ys.append(int(b["latency_us"]) / 1e6)
            else:
                xm.append(x)
        ax.scatter(xs, ys, s=4 if cls == "routine" else 9, color=col, linewidths=0,
                   label=cls, zorder=3 if cls == "urgent" else 2)
        if xm:
            ax.scatter(xm, [top * 0.8] * len(xm), s=9, facecolors="none", edgecolors=col,
                       linewidths=0.6, zorder=3)
    for slo in (20, 300):
        ax.axhline(slo, color=INK2, linestyle="--", linewidth=0.7, zorder=1)
    ax.set_yscale("log")
    ax.set_ylim(1, top)
    miss = sum(1 for b in bts if b["deadline_met"] != "1")
    ax.set_title(f"{title}\n{miss} of {len(bts)} frames late or dropped", fontsize=8, color=INK, loc="left")
    if show_ylabels:
        ax.set_ylabel("latency (s)", fontsize=7, color=INK2)

    # guard intervals from the tier-change log
    guard = []
    on = None
    for e in sorted(evs, key=lambda e: int(e["ts_us"])):
        g = e.get("guard") == "1"
        if g and on is None:
            on = m(e["ts_us"])
        elif not g and on is not None:
            guard.append((on, m(e["ts_us"]))); on = None
    if on is not None:
        guard.append((on, t_end))
    if not guard_on:
        guard = []

    # (b) queue depth, thresholds, guard
    ax = axes[1]
    style(ax)
    for a, b in guard:
        ax.axvspan(a, b, color=SHADE, zorder=0, linewidth=0, label="guard: lowest effort")
    pts = sorted((m(e["ts_us"]), int(e["ingest_depth"])) for e in exs if e["ingest_depth"])
    ax.step([p[0] for p in pts], [p[1] for p in pts], where="post", color=INK, linewidth=0.8)
    qm = {e["q_med"] for e in evs}; ql = {e["q_low"] for e in evs}
    for q, lab in ((qm, "$Q_1$"), (ql, "$Q_2$")):
        if adaptive and len(q) == 1:
            v = int(q.pop())
            ax.axhline(v, color=INK2, linestyle=":", linewidth=0.7)
            ax.text(t_end, v, " " + lab, fontsize=6, color=INK2, va="center")
    if show_ylabels:
        ax.set_ylabel("queued frames", fontsize=7, color=INK2)

    # (c) effort of module executions per bin
    ax = axes[2]
    style(ax)
    cnt = defaultdict(lambda: defaultdict(int))
    for e in exs:
        eff = SLOT_EFFORT.get(int(e["param_id"]) + 1)
        if eff:
            cnt[int(m(e["ts_us"]) * 60 // BIN_S)][eff] += 1
    bins = sorted(cnt)
    bottom = [0.0] * len(bins)
    for eff in ("low", "med", "high"):
        fr = [cnt[k][eff] / max(1, sum(cnt[k].values())) for k in bins]
        ax.bar([k * BIN_S / 60 for k in bins], fr, width=BIN_S / 60, bottom=bottom, align="edge",
               color=EFFORT_COL[eff], linewidth=0, label=eff)
        bottom = [b + f for b, f in zip(bottom, fr)]
    ax.set_ylim(0, 1)
    ax.set_xlabel("processing time (min)", fontsize=7, color=INK2)
    if show_ylabels:
        ax.set_ylabel("effort share", fontsize=7, color=INK2)
    for a_ in axes:
        a_.set_xlim(0, t_end)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("parsed_dir")
    ap.add_argument("runs", nargs="+", help="RUN_ID:Title")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    n = len(a.runs)
    fig, axes = plt.subplots(3, n, figsize=(3.4 * n, 4.6), sharex="col", sharey="row",
                             gridspec_kw={"height_ratios": [1.6, 1, 0.6]}, squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    for j, spec in enumerate(a.runs):
        run, _, title = spec.partition(":")
        panel(axes[:, j], a.parsed_dir, run, title or run, j == 0)
    h1, l1 = axes[0, 0].get_legend_handles_labels()
    h3, l3 = axes[2, 0].get_legend_handles_labels()
    hg, lg = [], []
    for ax in axes[1]:
        h, l = ax.get_legend_handles_labels()
        if h:
            hg, lg = h[:1], l[:1]
            break
    fig.legend(h1 + h3 + hg, l1 + l3 + lg, loc="lower center", ncol=6, fontsize=7, frameon=False,
               bbox_to_anchor=(0.5, -0.04), markerscale=2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    fig.savefig(a.out + ".pdf", bbox_inches="tight", facecolor=SURFACE)
    fig.savefig(a.out + ".png", bbox_inches="tight", facecolor=SURFACE, dpi=200)
    print("wrote", a.out + ".pdf")


if __name__ == "__main__":
    main()
