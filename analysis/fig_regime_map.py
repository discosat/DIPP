#!/usr/bin/env python3
"""Regime map from a policy_lab grid: worst regret against the best static
policy over frame periods and seeds, as a function of routine deadline and
burst length, one row per content set, one column per policy.

    python3 analysis/fig_regime_map.py analysis/lab/grid_fam6.csv --policies ladder-as-evaluated,C3 \
        -o analysis/fig/regime_map.pdf
"""
import argparse, csv, os, sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

SURFACE, INK, INK2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--policies", required=True)
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.csv)))
    pols = a.policies.split(",")
    slos = sorted({float(r["slo_r"]) for r in rows})
    bursts = sorted({float(r["burst_mean"]) for r in rows})
    contents = ["cloudy", "average", "clear"]

    def reg(r, p):
        return max(float(r[f"U:static-{e}"]) for e in ("low", "med", "high")) - float(r[f"U:{p}"])

    cmap = LinearSegmentedColormap.from_list("seq", ["#f4f3ef", "#f2c9a5", "#e07b39", "#9c3d10"])
    fig, axes = plt.subplots(len(contents), len(pols), figsize=(2.3 * len(pols) + 0.6, 1.9 * len(contents)),
                             sharex=True, sharey=True, squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    im = None
    for i, c in enumerate(contents):
        for j, p in enumerate(pols):
            ax = axes[i][j]
            M = [[max([reg(r, p) for r in rows if r["content"] == c and float(r["slo_r"]) == s
                       and float(r["burst_mean"]) == b] or [0.0]) for b in bursts] for s in slos]
            im = ax.imshow(M, origin="lower", aspect="auto", cmap=cmap, vmin=0, vmax=0.25)
            for yi, s in enumerate(slos):
                for xi, b in enumerate(bursts):
                    v = M[yi][xi]
                    ax.text(xi, yi, f"{v:.2f}".lstrip("0") if v >= 0.005 else "0", ha="center", va="center",
                            fontsize=5.5, color=INK if v < 0.17 else SURFACE)
            ax.set_xticks(range(len(bursts)))
            ax.set_xticklabels([f"{b:g}" for b in bursts], fontsize=6.5, color=INK2)
            ax.set_yticks(range(len(slos)))
            ax.set_yticklabels([f"{s:g}" for s in slos], fontsize=6.5, color=INK2)
            for sp in ax.spines.values():
                sp.set_visible(False)
            if i == 0:
                ax.set_title(p, fontsize=7.5, color=INK)
            if j == 0:
                ax.set_ylabel(f"{c}\nroutine deadline (s)", fontsize=7, color=INK2)
            if i == len(contents) - 1:
                ax.set_xlabel("burst length (frames)", fontsize=7, color=INK2)
    cb = fig.colorbar(im, ax=axes, shrink=0.6, pad=0.02)
    cb.ax.tick_params(labelsize=6.5, colors=INK2)
    cb.set_label("worst regret vs best static", fontsize=7, color=INK2)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    fig.savefig(a.out, bbox_inches="tight", facecolor=SURFACE)
    fig.savefig(a.out.rsplit(".", 1)[0] + ".png", bbox_inches="tight", facecolor=SURFACE, dpi=200)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
