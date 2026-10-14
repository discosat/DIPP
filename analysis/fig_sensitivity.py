#!/usr/bin/env python3
"""Threshold sensitivity figure (AE-1, R2.2): worst-case regret against the
best static policy, and the worst urgent hit rate, as the rule's constant k
varies.  python3 analysis/fig_sensitivity.py analysis/sim/k_sensitivity.csv -o analysis/fig/k_sensitivity.pdf

Two panels sharing the k axis (no second y axis). Stage and batch
granularity are the two series (categorical slots 1 and 2, markers differ
too, so identity is not colour alone); k = 2, the default, is marked; the
reference policies' worst-case regret is given in the caption text, not
drawn, since it would compress the curve (0.09-0.65).
"""
import argparse, csv, os, sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
STAGE, BATCH = "#2a78d6", "#eb6834"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.csv)))
    lad = [r for r in rows if r["policy"] == "ladder"]
    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(3.5, 3.6),
                                   gridspec_kw={"height_ratios": [1.3, 1], "hspace": 0.08})
    fig.patch.set_facecolor(SURFACE)
    for ax in (ax1, ax2):
        ax.set_facecolor(SURFACE)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color(MUTED)
        ax.tick_params(colors=INK2, labelsize=7.5)
        ax.grid(axis="y", color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        ax.axvline(2.0, color=MUTED, lw=0.8, ls=(0, (3, 3)), zorder=0)
    for gran, col, mk in (("stage", STAGE, "o"), ("batch", BATCH, "s")):
        g = sorted((float(r["k"]), r) for r in lad if r["granularity"] == gran)
        ks = [k for k, _ in g]
        ax1.plot(ks, [float(r["max_regret"]) for _, r in g], color=col, lw=1.5, marker=mk, ms=4,
                 markeredgecolor=SURFACE, markeredgewidth=0.6, label=f"{gran} granularity")
        ax2.plot(ks, [float(r["min_urgent_hit"]) for _, r in g], color=col, lw=1.5, marker=mk, ms=4,
                 markeredgecolor=SURFACE, markeredgewidth=0.6)
    ax1.set_ylabel("Worst-case regret", fontsize=8, color=INK2)
    ax1.set_ylim(bottom=0)
    ax2.set_ylabel("Worst urgent\nhit rate", fontsize=8, color=INK2)
    ax2.set_xlabel("k  (thresholds Q = ⌊SLO / (k · s_max)⌋)", fontsize=8, color=INK2)
    ax1.text(2.05, ax1.get_ylim()[1] * 0.95, "default k = 2", fontsize=7, color=INK2, va="top")
    leg = ax1.legend(fontsize=7, frameon=False, loc="upper right")
    for t in leg.get_texts():
        t.set_color(INK)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    fig.savefig(a.out, bbox_inches="tight", facecolor=SURFACE)
    fig.savefig(a.out.rsplit(".", 1)[0] + ".png", bbox_inches="tight", facecolor=SURFACE, dpi=200)
    ref = {r["policy"]: r["max_regret"] for r in rows if r["policy"] != "ladder"}
    print("reference worst-case regret (for the caption):", ref)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
