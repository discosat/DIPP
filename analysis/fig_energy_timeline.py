#!/usr/bin/env python3
"""Simulated timelines in an energy-deficit regime (analysis/lab_energy_deficit.py):
each frame's latency against its deadline, the battery above the survival floor
(the payload is shed at the floor and recharges), and the effort the modules
ran at.

    python3 analysis/fig_energy_timeline.py --regime dd3.5 --cell C1 --seed 1 \
        --runs v2:30 v2:1800 static-high:30 -o analysis/fig/energy_timeline
"""
import argparse, os, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, HERE)
import policy_lab as L  # noqa: E402
import sched_sim as S   # noqa: E402
import lab_energy_deficit as E  # noqa: E402
from lab_real import scenario, stable_seed  # noqa: E402

SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
ROUTINE, URGENT = "#2a78d6", "#eb6834"
EFFORT_COL = {"low": "#c9d9ee", "med": "#6f9fd8", "high": "#1d4f91"}
NAMES = {"v2": "SIPP default", "static-high": "Static high", "static-med": "Static medium",
         "static-low": "Static low", "greedy": "Deadline-only", "v2+ET": "SIPP + energy-timed"}
BIN_S = 120


def style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=INK2, labelsize=7)
    ax.grid(axis="y", color=GRID, linewidth=0.5)


def simulate(regime, cell, seed, pol_name, d_r, d_u):
    content, sets = S.build_content()
    pre, ct = E.CELLS[cell]
    arr = S.read_trace(os.path.join(ROOT, "experiments/traces", f"{pre}-s{seed}.trace"))
    sc = scenario(arr, sets[ct], content, d_r / 300.0, seed=stable_seed(cell, seed), slo_u=float(d_u))
    sc["energy"] = E.energy(regime, seed)
    sc["record"] = []
    pol = [p for p in E.policies() if p.name == pol_name][0]
    r = L.simulate(sc, pol)
    return r, sc["record"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regime", required=True)
    ap.add_argument("--cell", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--urgent", type=float, required=True)
    ap.add_argument("--runs", nargs="+", required=True, help="policy:routine_deadline_s")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    n = len(a.runs)
    fig, axes = plt.subplots(3, n, figsize=(3.4 * n, 4.8), sharex=True, sharey="row",
                             gridspec_kw={"height_ratios": [1.5, 1, 0.6]}, squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    for j, spec in enumerate(a.runs):
        pol, dr = spec.split(":")
        r, rec = simulate(a.regime, a.cell, a.seed, pol, float(dr), a.urgent)
        fr = r["frames"]
        t0 = min(x[0] for x in fr)
        m = lambda t: (t - t0) / 60.0
        ax = axes[0, j]; style(ax)
        for cls, col in (("routine", ROUTINE), ("urgent", URGENT)):
            xs = [m(f[0]) for f in fr if f[3] == cls and f[2] is not None]
            ys = [f[2] - f[0] for f in fr if f[3] == cls and f[2] is not None]
            ax.scatter(xs, ys, s=4 if cls == "routine" else 9, color=col, linewidths=0, label=cls)
            miss = [m(f[0]) for f in fr if f[3] == cls and f[2] is None]
            ax.scatter(miss, [3000] * len(miss), s=9, facecolors="none", edgecolors=col, linewidths=0.6)
        for d in (a.urgent, float(dr)):
            ax.axhline(d, color=INK2, ls="--", lw=0.7)
        ax.set_yscale("log"); ax.set_ylim(1, 5000)
        late = sum(1 for f in fr if f[2] is None or f[2] > f[1] + 1e-9)
        ax.set_title(f"{NAMES.get(pol, pol)}, routine {int(float(dr))} s\nU = {r['U']:.3f}, {late} of {len(fr)} late",
                     fontsize=8, color=INK, loc="left")
        ax = axes[1, j]; style(ax)
        ts = [m(x[0]) for x in rec if x[1] is not None]
        ws = [x[1] for x in rec if x[1] is not None]
        ax.plot(ts, ws, color=INK, lw=0.8)
        ax.axhline(0, color=URGENT, lw=0.7, ls=":")
        ax2 = axes[2, j]; style(ax2)
        cnt = defaultdict(lambda: defaultdict(int))
        for f in fr:
            if f[2] is None:
                continue
            for mod, e in f[4].items():
                cnt[int((f[2] - t0) // BIN_S)][e] += 1
        bins = sorted(cnt)
        bottom = [0.0] * len(bins)
        for e in ("low", "med", "high"):
            fr_e = [cnt[k][e] / max(1, sum(cnt[k].values())) for k in bins]
            ax2.bar([k * BIN_S / 60 for k in bins], fr_e, width=BIN_S / 60, bottom=bottom, align="edge",
                    color=EFFORT_COL[e], linewidth=0, label=e)
            bottom = [b + x for b, x in zip(bottom, fr_e)]
        ax2.set_ylim(0, 1)
        ax2.set_xlabel("time (min)", fontsize=7, color=INK2)
    axes[0, 0].set_ylabel("latency (s)", fontsize=7, color=INK2)
    axes[1, 0].set_ylabel("battery above\nfloor (Wh)", fontsize=7, color=INK2)
    axes[2, 0].set_ylabel("effort share", fontsize=7, color=INK2)
    h1, l1 = axes[0, 0].get_legend_handles_labels()
    h3, l3 = axes[2, 0].get_legend_handles_labels()
    fig.legend(h1 + h3, l1 + l3, loc="lower center", ncol=5, fontsize=7, frameon=False, bbox_to_anchor=(0.5, -0.03))
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    fig.savefig(a.out + ".pdf", bbox_inches="tight", facecolor=SURFACE)
    fig.savefig(a.out + ".png", bbox_inches="tight", facecolor=SURFACE, dpi=200)
    print("wrote", a.out + ".pdf")


if __name__ == "__main__":
    main()
