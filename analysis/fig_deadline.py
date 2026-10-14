#!/usr/bin/env python3
"""Figures for the grounded deadline sweep (analysis/lab_deadline.py).

    python3 analysis/fig_deadline.py analysis/lab/deadline.csv -o analysis/fig/

deadline_sweep: utility U (mean over seeds, exact cost model) against the
routine deadline, one panel per cell, urgent deadline 20 s; queue capacity 100
solid, 300 dashed (only where the deadline is 1800 s or more).
urgent_sweep: urgent hit rate (minimum over cells and seeds) against the
urgent deadline at a routine deadline of 1800 s, with SIPP's 17.9 s
worst-case urgent latency as a vertical line.
Colours and markers as in analysis/eval_report.py.
"""
import argparse, csv, os, statistics
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
POL = [("static-low", "Static low", "#9db8d9", "v"), ("static-med", "Static medium", "#6f93c2", "s"),
       ("static-high", "Static high", "#3d6aa8", "^"), ("greedy", "Deadline-only", "#eb6834", "D"),
       ("ladder-as-evaluated", "SIPP v1", "#8a8984", "P"), ("v2", "SIPP default", "#0b0b0b", "o")]
POL_FINAL = [x for x in POL if x[0] != "v2"] + [("v2-board", "SIPP (board-evaluated)", "#c9a227", "X"),
                                                  ("sipp", "SIPP default", "#0b0b0b", "o")]
CELLS = [("C1", "1×, cloudy"), ("C2", "1×, Earth avg."), ("C3", "1×, clear sky"),
         ("C4", "2×, Earth avg."), ("C5", "2×, clear sky")]
ROUTINE_TICKS = [(30, "30 s\nKodan"), (300, "5 min\nPhoenix"), (1800, "30 min\nCleanSeaNet"),
                 (3600, "\n\n1 h"), (21600, "6 h\ncontact"), (40320, "\n\n11.2 h\nmax. wait")]
URGENT_TICKS = [(10, "10 s"), (20, "20 s"), (60, "60 s\nDyn. Targ."), (300, "5 min\nalert")]
BOUND_S = 17.9


def style(ax):
    ax.set_facecolor(SURFACE)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(MUTED)
    ax.tick_params(colors=INK2, labelsize=7)
    ax.grid(axis="y", color=GRID, linewidth=0.6)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--cond", default="exact")
    a = ap.parse_args()
    R = [r for r in csv.DictReader(open(a.csv)) if r["cond"] == a.cond and r["cell"] in dict(CELLS)]
    global POL
    if R and "U:sipp" in R[0]:
        POL = POL_FINAL
    if not R:
        raise SystemExit(f"{a.csv}: no rows for cond={a.cond}")
    os.makedirs(a.outdir, exist_ok=True)

    # deadline_sweep
    U = defaultdict(list)
    for r in R:
        if r["D_u"] == "20":
            for p, *_ in POL:
                U[(r["cell"], int(r["D_r"]), int(r["cap"]), p)].append(float(r[f"U:{p}"]))
    fig, axes = plt.subplots(1, len(CELLS), figsize=(13, 2.9), sharey=True)
    fig.patch.set_facecolor(SURFACE)
    for ax, (c, title) in zip(axes, CELLS):
        style(ax)
        for p, name, col, mk in POL:
            for cap, ls in ((100, "-"), (300, "--")):
                pts = sorted((d, statistics.mean(v)) for (cc, d, cp, pp), v in U.items()
                             if cc == c and cp == cap and pp == p)
                if not pts:
                    continue
                ax.plot([x for x, _ in pts], [y for _, y in pts], ls=ls, color=col, lw=1.3 if p in ("v2", "sipp") else 0.9,
                        marker=mk if cap == 100 else None, ms=3.5, label=name if cap == 100 else None, zorder=3 if p in ("v2", "sipp") else 2)
        ax.set_xscale("log")
        ax.set_xticks([x for x, _ in ROUTINE_TICKS])
        ax.set_xticklabels([l for _, l in ROUTINE_TICKS], fontsize=5.5, color=INK2)
        ax.minorticks_off()
        ax.set_title(title, fontsize=8, color=INK)
        ax.set_ylim(0, 1.03)
    axes[0].set_ylabel("Utility within deadline, U", fontsize=8, color=INK2)
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=6, fontsize=7, frameon=False, bbox_to_anchor=(0.5, -0.1))
    fig.text(0.995, 0.01, "routine deadline (urgent 20 s); dashed: queue capacity 300", ha="right", fontsize=6.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    out = os.path.join(a.outdir, "deadline_sweep")
    fig.savefig(out + ".pdf", bbox_inches="tight", facecolor=SURFACE)
    fig.savefig(out + ".png", bbox_inches="tight", facecolor=SURFACE, dpi=200)

    # urgent_sweep
    H = defaultdict(list)
    for r in R:
        if r["D_r"] == "1800" and r["cap"] == "100":
            for p, *_ in POL:
                H[(int(r["D_u"]), p)].append(float(r[f"urg:{p}"]))
    fig, ax = plt.subplots(figsize=(4.2, 2.8))
    fig.patch.set_facecolor(SURFACE)
    style(ax)
    for p, name, col, mk in POL:
        pts = sorted((d, min(v)) for (d, pp), v in H.items() if pp == p)
        ax.plot([x for x, _ in pts], [y for _, y in pts], color=col, marker=mk, ms=4, lw=1.3 if p in ("v2", "sipp") else 0.9,
                label=name, zorder=3 if p in ("v2", "sipp") else 2)
    ax.axvline(BOUND_S, color=MUTED, lw=0.8, ls=(0, (3, 3)))
    ax.text(BOUND_S * 1.05, 0.03, "SIPP worst case 17.9 s", fontsize=6, color=INK2)
    ax.set_xscale("log")
    ax.set_xticks([x for x, _ in URGENT_TICKS])
    ax.set_xticklabels([l for _, l in URGENT_TICKS], fontsize=6.5, color=INK2)
    ax.minorticks_off()
    ax.set_ylim(0, 1.03)
    ax.set_xlabel("urgent deadline (routine 30 min)", fontsize=7.5, color=INK2)
    ax.set_ylabel("urgent hit rate (worst cell, seed)", fontsize=7.5, color=INK2)
    ax.legend(fontsize=6, frameon=False, loc="lower right", ncol=2)
    fig.tight_layout()
    out = os.path.join(a.outdir, "urgent_sweep")
    fig.savefig(out + ".pdf", bbox_inches="tight", facecolor=SURFACE)
    fig.savefig(out + ".png", bbox_inches="tight", facecolor=SURFACE, dpi=200)
    print("wrote", os.path.join(a.outdir, "deadline_sweep.pdf"), "and urgent_sweep.pdf")


if __name__ == "__main__":
    main()
