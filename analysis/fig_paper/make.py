#!/usr/bin/env python3
"""Data figures for the revised SIPP paper (plan: paper-rev/FIGURE_PLAN.md).

    python3 analysis/fig_paper/make.py [f5 f6 f7 f8 f9]     # default: all

Writes analysis/fig_paper/<name>.{pdf,png}. Sizes are IEEE column widths; text is
8 pt serif so the figures sit with the body text at 100 %.

Colour: three hues for policy families (SIPP, deadline-only, fixed designs),
validated for all pairs; greys with distinct markers for the static levels; one
sequential blue ramp for effort. Every series also has its own marker shape, so
the figures survive greyscale print.

Inputs (all relative to analysis/):
  f5 parsed/eval_final/batches.csv, board-fixed/parsed/batches.csv
  f6 board-fixed/ALL_BOARD.md, lab/oracle_lp_fixed013.csv
  f7 parsed/board-grid/batches.csv, parsed/eval_final/batches.csv
  f8 parsed/board-grid/executions.csv, parsed/board-grid/battery.csv
  f9 board-grid/all/runs.csv, board_grid_predictions_fixed.csv, lab/final3/{heldout,deadline}.csv
"""
import csv, os, re, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
AN = os.path.dirname(HERE)
ONE, TWO = 3.5, 7.16          # IEEE column widths, inches

INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
SIPP, DLONLY, DESIGN = "#2a78d6", "#eb6834", "#1baf7a"
EFFORT = {"low": "#c9d9ee", "med": "#6f9fd8", "high": "#1d4f91"}
SLOT_STAGE = {3: ("cls", "low"), 4: ("cls", "med"), 5: ("cls", "high"),
              6: ("seg", "low"), 19: ("seg", "med"), 7: ("seg", "high"),
              16: ("enc", "low"), 17: ("enc", "med"), 18: ("enc", "high")}
# series: colour, marker, face (None = hollow), size, label
SER = {
    "low":   ("#8a8984", "v", None, 3.6, "Static low"),
    "med":   ("#52514e", "D", None, 3.0, "Static medium"),
    "high":  ("#0b0b0b", "^", None, 3.6, "Static high"),
    "dl":    (DLONLY, "s", DLONLY, 3.0, "Deadline-only"),
    "avg":   (DESIGN, "P", DESIGN, 4.2, "Fixed design, average"),
    "worst": (DESIGN, "X", DESIGN, 4.2, "Fixed design, worst case"),
    "sipp":  (SIPP, "o", SIPP, 4.6, "SIPP"),
}
CONTENT = {"C1": "1× cloudy", "C2": "1× Earth avg.", "C3": "1× clear",
           "C4": "2× Earth avg.", "C5": "2× clear"}

plt.rcParams.update({
    "font.family": "Liberation Serif", "font.size": 8, "axes.labelsize": 8,
    "axes.titlesize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "legend.fontsize": 7, "axes.edgecolor": MUTED, "axes.linewidth": 0.6,
    "xtick.color": INK2, "ytick.color": INK2, "axes.labelcolor": INK,
    "xtick.major.width": 0.5, "ytick.major.width": 0.5, "xtick.major.size": 2.5,
    "ytick.major.size": 2.5, "pdf.fonttype": 42, "ps.fonttype": 42,
    "axes.spines.top": False, "axes.spines.right": False,
    "legend.frameon": False, "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
})


def rd(path):
    p = os.path.join(AN, path)
    if not os.path.exists(p):
        sys.exit(f"missing input {p}")
    with open(p) as f:
        return list(csv.DictReader(f))


def save(fig, name):
    for ext, kw in (("pdf", {}), ("png", {"dpi": 300})):
        fig.savefig(os.path.join(HERE, f"{name}.{ext}"), **kw)
    plt.close(fig)
    print("wrote", name)


def dot(ax, x, y, key, z=3, **kw):
    col, m, face, ms, _ = SER[key]
    ax.plot(x, y, ls="none", marker=m, ms=ms, mfc=face if face else "white", mec=col if face is None else INK,
            mew=0.7 if face is None else 0.3, zorder=z, **kw)


def handle(key):
    col, m, face, ms, lab = SER[key]
    return Line2D([], [], ls="none", marker=m, ms=ms, mfc=face if face else "white",
                  mec=col if face is None else INK, mew=0.7 if face is None else 0.3, label=lab)


# ---- F5: why no fixed effort fits -------------------------------------------------
def f5():
    ev = rd("parsed/eval_final/batches.csv")
    fx = rd("board-fixed/parsed/batches.csv")
    OVERHEAD_S = 0.78  # per-frame process and I/O overhead, DIPP_SCHED_FRAME_OVERHEAD_MS

    def times(rows, run):
        v = [float(r["exec_us_sum"]) / 1e6 + OVERHEAD_S for r in rows
             if r["run_id"] == run and r["exec_us_sum"] and float(r["exec_us_sum"]) > 0 and r["completed"] == "1"]
        if not v:
            sys.exit(f"f5: no frames for {run}")
        return np.array(v)

    table = {}
    for key in ("low", "med", "high"):
        for c in ("C1", "C2", "C3"):
            table[key, c] = times(ev, f"{c}-{key}-s1")
    table["avg", "C2"] = times(fx, "P2C4-avg-s1")
    table["avg", "C3"] = times(fx, "P1C3-avg-s1")
    table["worst", "C2"] = times(fx, "P1C4-worst-s1")
    table["worst", "C3"] = times(fx, "P3C5-worst-s1")
    fig, ax = plt.subplots(figsize=(ONE, 2.1))
    pos = {"C1": 0, "C2": 1, "C3": 2}
    keys = ("low", "med", "high", "avg", "worst")
    w = 0.165
    style = {"low": dict(facecolor=EFFORT["low"]), "med": dict(facecolor=EFFORT["med"]), "high": dict(facecolor=EFFORT["high"]),
             "avg": dict(facecolor="white", edgecolor=DESIGN, hatch="////", linewidth=0.7),
             "worst": dict(facecolor="white", edgecolor=DESIGN, hatch="xxxx", linewidth=0.7)}
    for i, key in enumerate(keys):
        for c in ("C1", "C2", "C3"):
            if (key, c) not in table:
                continue
            t = table[key, c]
            x = pos[c] + (i - 2) * (w + 0.012)
            lo, hi = np.percentile(t, [5, 95])
            ax.bar(x, t.mean(), width=w, zorder=2, **style[key])
            ax.plot([x, x], [lo, hi], color=INK, lw=0.6, zorder=3)
            print(f"  f5 {key:5} {c}: mean {t.mean():5.2f} s  p5-p95 {lo:.2f}-{hi:.2f}  n={len(t)}")
    for y, lab in ((6.68, "1× load\n6.68 s"), (3.34, "2× load\n3.34 s")):
        ax.axhline(y, color=INK, lw=0.7, ls=(0, (4, 2)), zorder=4)
        ax.text(2.53, y, lab, fontsize=7, color=INK, va="center", ha="left", linespacing=0.95, clip_on=False)
    ax.set_xticks([0, 1, 2], ["Cloudy", "Earth average", "Clear sky"])
    ax.set_xlim(-0.5, 2.5)
    ax.set_ylim(0, 14)
    ax.set_ylabel("Processing time per frame (s)")
    ax.grid(axis="y", color=GRID, lw=0.4)
    ax.set_axisbelow(True)
    ax.tick_params(axis="x", length=0)
    from matplotlib.patches import Patch
    names = {"low": "Static low", "med": "Static medium", "high": "Static high",
             "avg": "Fixed design, average", "worst": "Fixed design, worst case"}
    ax.legend(handles=[Patch(label=names[k], **style[k]) for k in keys], ncol=3, loc="lower center",
              bbox_to_anchor=(0.5, 1.0), columnspacing=0.9, handlelength=1.2, handletextpad=0.4)
    save(fig, "f5_frame_time")


# ---- F6: U per condition -----------------------------------------------------------
def f6():
    p = os.path.join(AN, "board-fixed/ALL_BOARD.md")
    rows = [ln.strip("|\n ").split("|") for ln in open(p) if re.match(r"\| (E37)?P\dC\d \|", ln)]
    if len(rows) != 17:
        sys.exit(f"f6: expected 17 conditions, got {len(rows)}")
    num = lambda s: None if s.strip() in ("–", "") else float(s.replace("*", "").strip())
    # Upper bound: LP bound for seed 1 (the seed of the board values), solved with the board's
    # minimum per-frame overhead (0.13 s, oracle.py --fixed-s) so hardware cannot exceed it.
    ub = {r["cell"]: float(r["UB_LP"]) for r in rd("lab/oracle_lp_fixed013.csv") if r["seed"] == "1"}
    d = {}
    for r in rows:
        c = r[0].strip()
        d[c] = dict(low=num(r[1]), med=num(r[2]), high=num(r[3]), avg=num(r[4]), worst=num(r[5]),
                    dl=num(r[6]), sipp=num(r[7]), ub=ub[c])
        over = {k: v for k, v in d[c].items() if k != "ub" and v is not None and v > d[c]["ub"]}
        if over:
            print(f"  f6 {c}: board value above the bound {d[c]['ub']:.4f}: {over}")
    groups = [("20 s / 30 s", ["P1C2", "P1C3", "P1C4", "P1C5"]),
              ("20 s / 5 min", ["P2C1", "P2C2", "P2C3", "P2C4", "P2C5"]),
              ("60 s / 30 min", ["P3C2", "P3C3", "P3C4", "P3C5"]),
              ("3.7 W, 20 s / 5 min", ["E37P2C2", "E37P2C3"]),
              ("3.7 W, 5 min / 6 h", ["E37P4C2", "E37P4C3"])]
    fig, ax = plt.subplots(figsize=(ONE, 4.1))
    y, ticks, labs = 0, [], []
    for g, cells in groups:
        ax.text(-0.005, y - 0.05, g, fontsize=7, fontweight="bold", color=INK, va="center", ha="left",
                transform=ax.get_yaxis_transform())
        y += 1
        for c in cells:
            v = d[c]
            ax.axhline(y, color=GRID, lw=0.4, zorder=0)
            ax.plot([v["ub"]] * 2, [y - 0.38, y + 0.38], color=INK, lw=1.0, zorder=2, solid_capstyle="butt")
            for k in ("low", "med", "high", "dl", "avg", "worst"):
                if v[k] is not None:
                    dot(ax, [v[k]], [y], k, z=3)
            dot(ax, [v["sipp"]], [y], "sipp", z=5)
            ticks.append(y)
            labs.append(CONTENT[c[-2:]])
            y += 1
        y += 0.35
    ax.set_yticks(ticks, labs)
    ax.set_ylim(y - 0.6, -0.8)
    ax.set_xlim(0, 1.03)
    ax.set_xticks(np.arange(0, 1.01, 0.2))
    ax.set_xlabel("Utility within deadline U")
    ax.grid(axis="x", color=GRID, lw=0.4)
    ax.set_axisbelow(True)
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    hs = [handle(k) for k in ("sipp", "dl", "low", "med", "high", "avg", "worst")]
    hs.append(Line2D([], [], color=INK, lw=1.0, marker="|", ms=6, ls="none", label="Upper bound (oracle)"))
    ax.legend(handles=hs, ncol=2, loc="lower center", bbox_to_anchor=(0.42, 1.0), columnspacing=1.0,
              handletextpad=0.2, labelspacing=0.25)
    save(fig, "f6_grid")


# ---- F7: latency against deadlines -------------------------------------------------
def f7():
    bg, ev = rd("parsed/board-grid/batches.csv"), rd("parsed/eval_final/batches.csv")
    panels = [("20 s / 30 s, 1× clear", bg, ("P1C3-final-s1", "P1C3-low-s1", "P1C3-greedy-s1")),
              ("20 s / 5 min, 2× clear", None, (("P2C5-final-s1", bg), ("C5-low-s1", ev), ("C5-greedy-s1", ev))),
              ("3.7 W, 20 s / 5 min, 1× clear", bg, ("E37P2C3-final-s1", "E37P2C3-med-s1", "E37P2C3-greedy-s1"))]
    best = ("Best static", "Best static", "Best static")
    fig, axes = plt.subplots(2, 3, figsize=(TWO, 2.75), sharey=True)
    for j, (title, src, runs) in enumerate(panels):
        spec = runs if src is None else [(r, src) for r in runs]
        for i, cls in enumerate(("urgent", "routine")):
            ax = axes[i][j]
            dl = None
            for (run, rows), (col, ls, lab, lw) in zip(spec, ((SIPP, "-", "SIPP", 1.4),
                                                              (INK2, (0, (3, 1.5)), best[j], 1.0),
                                                              (DLONLY, "-", "Deadline-only", 1.0))):
                fr = [r for r in rows if r["run_id"] == run and r["cls"] == cls]
                if not fr:
                    sys.exit(f"f7: no {cls} frames in {run}")
                dl = float(fr[0]["slo_s"])
                lat = sorted(float(r["latency_us"]) / 1e6 for r in fr if r["completed"] == "1" and r["latency_us"])
                n = len(fr)
                x = np.array([0.5] + lat + [2000.0])
                y = np.array([0.0] + [(k + 1) / n for k in range(len(lat))] + [len(lat) / n])
                ax.step(x, y, where="post", color=col, ls=ls, lw=lw, label=lab, zorder=4 if lab == "SIPP" else 3)
                miss = sum(1 for r in fr if r["deadline_met"] != "1")
                print(f"  f7 {title:30} {cls:7} {lab:22} n={n} late_or_undone={miss} done={len(lat)}")
            ax.axvline(dl, color=INK, lw=0.7, ls=(0, (1, 1.5)), zorder=2)
            ax.text(dl * 1.07, 0.04, "deadline", fontsize=6.5, color=INK2, va="bottom", ha="left")
            ax.set_xscale("log")
            ax.set_xlim(2, 2000)
            ax.set_ylim(0, 1.03)
            ax.set_xticks([10, 100, 1000], ["10", "100", "1000"])
            ax.set_yticks([0, 0.5, 1], ["0", "0.5", "1"])
            ax.grid(color=GRID, lw=0.4)
            ax.set_axisbelow(True)
            if i == 0:
                ax.set_title(title, pad=3)
            else:
                ax.set_xlabel("Latency from capture (s)")
            if j == 0:
                ax.set_ylabel(f"{cls.capitalize()} frames\n(fraction delivered)")
    fig.tight_layout(pad=0.3, w_pad=0.6, h_pad=0.5, rect=(0, 0, 1, 0.91))
    hs, ls_ = axes[0][0].get_legend_handles_labels()
    ls_[1] = "Best static (low, low, medium)"
    fig.legend(hs, ls_, ncol=3, loc="upper center", columnspacing=1.6, handlelength=2.2)
    save(fig, "f7_latency")


# ---- F8: what the policy does over time --------------------------------------------
def f8():
    ex, bt = rd("parsed/board-grid/executions.csv"), rd("parsed/board-grid/battery.csv")
    ba = rd("parsed/board-grid/batches.csv")
    floor = 2 * 13.07 * 95.43 / 60.0
    orbit_s, phase0 = 95.43 * 60.0, 0.1202
    ecl = (0.625, 1.0)     # disco2_fig7-3_bol.profile: no generation at these orbit phases
    GAPW = 9.0             # drawn width of the idle gap, in x units (minutes of pass time)
    PAD = 1.0
    cols = [("P2C5-final-s1", "Overload: 20 s / 5 min, 2× clear sky, DISCO-2 power", True, (83.0, 93.0)),
            ("E37P2C3-final-s1", "Energy deficit: 20 s / 5 min, 1× clear sky, 3.7 W", False, (41.5, 42.2))]
    fig = plt.figure(figsize=(TWO, 3.15))
    gs = fig.add_gridspec(4, 2, height_ratios=[0.13, 1.05, 0.8, 0.72], hspace=0.13, wspace=0.14)
    for j, (run, title, has_ecl, ylim) in enumerate(cols):
        rows = [r for r in ex if r["run_id"] == run]
        batt = [r for r in bt if r["run_id"] == run and r["tag"] in ("BATTERY", "BATTERY_FF")]
        arr = sorted(float(r["arrival_sim_t_s"]) / 60 for r in ba if r["run_id"] == run)
        if not rows or not batt or not arr:
            sys.exit(f"f8: no data for {run}")
        tm, wh = [], []
        for r in batt:
            ts = int(r["ts_us"])
            if r["tag"] == "BATTERY":
                tm.append((ts, float(r["sim_t_s"])))
                wh.append((float(r["sim_t_s"]), float(r["wh"])))
            else:
                tm += [(ts, float(r["sim_t_before_s"])), (ts + 1, float(r["sim_t_after_s"]))]
                wh += [(float(r["sim_t_before_s"]), float(r["wh_before"])),
                       (float(r["sim_t_after_s"]), float(r["wh_after"]))]
        tm.sort(); wh.sort()
        ts_a, sim_a = np.array([a for a, _ in tm], float), np.array([b for _, b in tm])
        t = np.interp([int(r["ts_us"]) for r in rows], ts_a, sim_a) / 60.0
        # the two passes keep their scale; the idle gap between them is squeezed to GAPW
        i_gap = int(np.argmax(np.diff(np.sort(t))))
        st = np.sort(t)
        g0, g1 = st[i_gap] + PAD, st[i_gap + 1] - PAD
        end = st[-1]
        xk = [0.0, g0, g0 + GAPW, g0 + GAPW + (end - g1)]
        mp = lambda v: np.interp(v, [0.0, g0, g1, end], xk)
        print(f"  f8 {run}: pass 1 to {g0:.1f} min, gap {g1 - g0:.1f} min, end {end:.1f}; "
              f"arrivals {len(arr)}; battery {min(b for _, b in wh):.2f}-{max(b for _, b in wh):.2f} Wh")
        n1 = sum(1 for a in arr if a < g0)
        ax_a = fig.add_subplot(gs[0, j])
        a0, a1, a2 = [fig.add_subplot(gs[i, j]) for i in (1, 2, 3)]
        axs = (ax_a, a0, a1, a2)
        for a in axs:
            a.set_xlim(0, xk[-1])
            a.axvspan(xk[1], xk[2], color="#f3f2ee", lw=0, zorder=0)
        if has_ecl:
            e0 = mp((ecl[0] - phase0) * orbit_s / 60)
            e1 = mp((ecl[1] - phase0) * orbit_s / 60)
            for a in axs:
                a.axvspan(e0, e1, color="#d9d8d2", lw=0, zorder=0.5)
            a0.text((e0 + e1) / 2, 0.97, "eclipse", fontsize=6.5, color=INK2, ha="center", va="top",
                    transform=a0.get_xaxis_transform(), rotation=90)
        a0.text((xk[1] + xk[2]) / 2, 0.03, f"{g1 - g0 + 2 * PAD:.0f} min\nidle", fontsize=6.5, color=INK2, ha="center", va="bottom",
                transform=a0.get_xaxis_transform(), linespacing=0.95, zorder=6)
        # top strip: when frames arrive
        spans = [[arr[0], arr[0]]]
        for v in arr[1:]:
            if v - spans[-1][1] > 0.5:
                spans.append([v, v])
            else:
                spans[-1][1] = v
        for s0, s1 in spans:
            x0, x1 = mp(s0), mp(s1)
            ax_a.barh(0.5, max(x1 - x0, 0.25), left=x0, height=0.9, color=INK, lw=0)
        ax_a.set_ylim(0, 1); ax_a.set_yticks([]); ax_a.axis("off")
        ax_a.set_title(title, pad=11)
        ax_a.text(mp(0.0), 1.15, f"Pass 1: {n1} frames arrive", fontsize=6.5, color=INK2, va="bottom", transform=ax_a.get_xaxis_transform())
        ax_a.text(xk[3], 1.15, f"Pass 2: {len(arr) - n1} frames", fontsize=6.5, color=INK2, va="bottom", ha="right", transform=ax_a.get_xaxis_transform())
        ord_ = np.argsort(t)
        depth = np.array([int(r["ingest_depth"]) + int(r["partial_depth"]) for r in rows])
        for lo, hi in ((0, g0), (g1, end + 1)):
            m = (t[ord_] >= lo) & (t[ord_] <= hi)
            a0.plot(mp(t[ord_][m]), depth[ord_][m], color=INK2, lw=0.7, zorder=3)
        a1.plot(mp(np.array([a for a, _ in wh]) / 60.0), [b for _, b in wh], color=SIPP, lw=1.0, zorder=3)
        a1.set_ylim(*ylim)
        for s_, row in (("cls", 2), ("seg", 1), ("enc", 0)):
            for e in ("low", "med", "high"):
                x = [ti for ti, r in zip(t, rows) if SLOT_STAGE.get(int(r["param_id"]) + 1) == (s_, e)]
                a2.vlines(mp(np.array(x)), row - 0.42, row + 0.42, color=EFFORT[e], lw=0.35, zorder=3)
        a2.set_yticks([0, 1, 2], ["Encoder", "Segmentation", "Classification"] if j == 0 else [])
        a2.set_ylim(-0.6, 2.6)
        a2.tick_params(axis="y", length=0)
        a2.spines["left"].set_visible(False)
        tk = [v for v in range(0, 30, 10) if v <= g0] + [v for v in range(100, 130, 10) if v <= end]
        a2.set_xticks(mp(np.array(tk, float)), [str(v) for v in tk])
        for a in (a0, a1):
            a.set_xticks(mp(np.array(tk, float)))
            a.tick_params(labelbottom=False)
            a.grid(axis="y", color=GRID, lw=0.4)
            a.set_axisbelow(True)
        for x_ in xk[1:3]:     # axis-break marks
            a2.plot([x_ - 0.5, x_ + 0.5], [-0.09, 0.0], color=INK2, lw=0.6, clip_on=False, transform=a2.get_xaxis_transform(), zorder=6)
        if j == 0:
            a0.set_ylabel("Frames queued")
            a1.set_ylabel("Battery (Wh)")
            a1.text(mp(1.0), 83.4, "floor (41.6 Wh) far below the axis", fontsize=6.5, color=INK2, va="bottom")
        else:
            a1.axhline(floor, color=INK, lw=0.7, ls=(0, (1, 1.5)), zorder=2)
            a1.text(xk[3], floor + 0.012, "floor", fontsize=6.5, color=INK2, ha="right", va="bottom")
    hs = [Line2D([], [], color=EFFORT[e], lw=3, label=f"{e.capitalize() if e != 'med' else 'Medium'} effort")
          for e in ("low", "med", "high")]
    from matplotlib.patches import Patch
    hs.append(Patch(facecolor="#f3f2ee", edgecolor="none", label="Idle gap (axis compressed)"))
    hs.append(Patch(facecolor="#d9d8d2", edgecolor="none", label="Eclipse"))
    fig.legend(handles=hs, ncol=5, loc="lower center", bbox_to_anchor=(0.5, 0.965), columnspacing=1.2, handlelength=1.5)
    fig.supxlabel("Orbital time (min)", fontsize=8, y=0.015)
    save(fig, "f8_timeline")


# ---- F9: beyond the board grid -----------------------------------------------------
def f9():
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(TWO, 2.3), gridspec_kw={"wspace": 0.42})
    for x_ in (a, b, c):
        x_.set_box_aspect(1)
    # (a) simulator vs board, the 83 runs
    board = {r["run"]: float(r["U"]) for r in rd("board-grid/all/runs.csv")}
    pred = {r["label"]: float(r["U_pred"]) for r in rd("board_grid_predictions_fixed.csv")}
    common = sorted(set(board) & set(pred))
    if len(common) != 83:
        sys.exit(f"f9: {len(common)} board runs have predictions, expected 83")
    final = [r for r in common if "-final-" in r]
    other = [r for r in common if "-final-" not in r]
    within = sum(abs(board[r] - pred[r]) <= 0.03 for r in common)
    print(f"  f9a within 0.03: {within}/83; final worst {max(abs(board[r]-pred[r]) for r in final):.3f}")
    a.fill_between([0, 1.03], [-0.03, 1.0], [0.03, 1.06], color="#ecebe6", lw=0, zorder=0)
    a.plot([0, 1.03], [0, 1.03], color=MUTED, lw=0.6, zorder=1)
    a.plot([pred[r] for r in other], [board[r] for r in other], ls="none", marker="o", ms=2.6, mfc="white", mec=INK2, mew=0.5, zorder=2, label="Static, deadline-only")
    a.plot([pred[r] for r in final], [board[r] for r in final], ls="none", marker="o", ms=3.0, mfc=SIPP, mec=INK, mew=0.25, zorder=3, label="SIPP")
    a.set_xlim(0, 1.03); a.set_ylim(0, 1.03)
    a.set_xticks([0, 0.5, 1]); a.set_yticks([0, 0.5, 1])
    a.set_xlabel("Predicted U (simulator)")
    a.set_ylabel("Measured U (board)")
    a.set_title(f"(a) {within} of 83 runs within 0.03", pad=3)
    a.legend(loc="lower right", handletextpad=0.1, borderaxespad=0.1, labelspacing=0.15)
    # (b) regret over 3,000 held-out missions
    held = rd("lab/final3/heldout.csv")
    best = np.array([max(float(r[f"U:static-{e}"]) for e in ("low", "med", "high")) for r in held])
    ser = [("U:sipp", SIPP, "-", 1.5, "SIPP"), ("U:greedy", DLONLY, "-", 1.0, "Deadline-only"),
           ("U:static-low", "#8a8984", (0, (3, 1.5)), 1.0, "Static low"),
           ("U:static-med", "#52514e", (0, (3, 1.5)), 1.0, "Static medium"),
           ("U:static-high", INK, (0, (1, 1)), 1.0, "Static high")]
    for col, c_, ls, lw, lab in ser:
        reg = np.sort(best - np.array([float(r[col]) for r in held]))
        b.step(reg, (np.arange(len(reg)) + 1) / len(reg), where="post", color=c_, ls=ls, lw=lw, label=lab, zorder=4 if lab == "SIPP" else 3)
        print(f"  f9b {lab:14} n={len(reg)} p50 {np.percentile(reg,50):+.3f} p99 {np.percentile(reg,99):.3f} max {reg.max():.3f}")
    b.set_xlim(-0.1, 0.8); b.set_ylim(0, 1.02)
    b.set_xlabel("Regret against best static")
    b.set_ylabel("Fraction of missions")
    b.set_title("(b) 3,000 simulated missions", pad=3)
    b.grid(color=GRID, lw=0.4); b.set_axisbelow(True)
    b.legend(loc="lower right", labelspacing=0.1, handlelength=1.5, borderaxespad=0.05, fontsize=6)
    # (c) worst regret per routine deadline
    dl = [r for r in rd("lab/final3/deadline.csv") if r["cond"] == "exact" and r["cap"] == "100" and r["D_u"] == "20"]
    if not dl:
        sys.exit("f9: no deadline rows after filtering")
    by = defaultdict(lambda: defaultdict(list))
    for r in dl:
        bs = max(float(r[f"U:static-{e}"]) for e in ("low", "med", "high"))
        for col in ("U:sipp", "U:greedy", "U:static-low", "U:static-med", "U:static-high"):
            by[int(r["D_r"])][col].append(bs - float(r[col]))
    xs = sorted(by)
    print("  f9c routine deadlines", xs, "rows per deadline", [len(by[x]['U:sipp']) for x in xs])
    for col, c_, ls, lw, lab in ser:
        ys = [max(by[x][col]) for x in xs]
        mk = {"SIPP": "o"}.get(lab, None)
        c.plot(xs, ys, color=c_, ls=ls, lw=lw, marker=mk, ms=3, zorder=4 if lab == "SIPP" else 3)
        print(f"  f9c {lab:14}", [round(v, 3) for v in ys])
    c.set_xscale("log")
    c.set_xticks(xs, [{30: "30 s", 300: "5 min", 1800: "30 min", 3600: "1 h", 21600: "6 h", 40320: "11 h"}.get(x, str(x)) for x in xs], rotation=45, ha="right", rotation_mode="anchor")
    c.minorticks_off()
    c.set_ylim(-0.02, 1.0)
    c.set_xlabel("Routine deadline")
    c.set_ylabel("Worst regret")
    c.set_title("(c) By routine deadline", pad=3)
    c.grid(color=GRID, lw=0.4); c.set_axisbelow(True)
    save(fig, "f9_generalisation")


FIGS = {"f5": f5, "f6": f6, "f7": f7, "f8": f8, "f9": f9}
if __name__ == "__main__":
    for k in sys.argv[1:] or FIGS:
        print(k)
        FIGS[k]()
