#!/usr/bin/env python3
"""One trace run on the orbit's own clock: the battery against its margin,
and every frame's latency against its SLO.

    python3 analysis/fig_timeline.py PARSED_DIR RUN_ID -o fig/timeline_<run>.pdf

PARSED_DIR is analysis/parse_runs.py output (battery.csv, batches.csv,
events.csv, runs.csv). The x axis is simulated time (minutes since the run
started): wall time while the daemon works, plus every fast-forward. Wall-
clock events (halts) are placed on it through the BATTERY / BATTERY_FF
lines, which pair the two clocks.

Top: battery energy (Wh) with the payload margin (dashed) and eclipse shaded.
Bottom: per-frame latency (s, log) at the frame's arrival, routine and urgent
in the first two categorical slots, their SLOs as dashed lines in the same
ink as the axes, frames that never completed as hollow markers at the top,
and the intervals the scheduler was halted on energy shaded.
"""
import argparse, bisect, csv, os, sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Reference palette (dataviz skill references/palette.md), light mode.
SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
ROUTINE, URGENT = "#2a78d6", "#eb6834"      # categorical slots 1, 2
SHADE = "#ecebe6"


def rows(path, run):
    if not os.path.exists(path):
        return []
    return [r for r in csv.DictReader(open(path)) if r.get("run_id") == run]


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("parsed_dir")
    ap.add_argument("run_id")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    bat = rows(os.path.join(a.parsed_dir, "battery.csv"), a.run_id)
    bts = rows(os.path.join(a.parsed_dir, "batches.csv"), a.run_id)
    evs = rows(os.path.join(a.parsed_dir, "events.csv"), a.run_id)
    run = rows(os.path.join(a.parsed_dir, "runs.csv"), a.run_id)
    if not bat or not bts:
        raise SystemExit(f"{a.run_id}: no battery or batch rows in {a.parsed_dir}")
    margin = f(run[0].get("cfg_margin_wh")) if run else None
    if margin is None:
        raise SystemExit(f"{a.run_id}: runs.csv has no cfg_margin_wh (BATTERY_CONFIG missing?)")

    # The battery series and the wall -> simulated clock pairs.
    pts, clock = [], []                  # (sim_s, wh, state), (ts_us, sim_s)
    for r in bat:
        ts = f(r.get("ts_us"))
        if r["tag"] == "BATTERY":
            s, wh = f(r["sim_t_s"]), f(r["wh"])
            pts.append((s, wh, r.get("state")))
            clock.append((ts, s))
        elif r["tag"] == "BATTERY_FF":
            s0, s1 = f(r["sim_t_before_s"]), f(r["sim_t_after_s"])
            pts.append((s0, f(r["wh_before"]), None)); pts.append((s1, f(r["wh_after"]), r.get("state")))
            clock.append((ts, s1))
    pts.sort(key=lambda p: p[0]); clock.sort()
    cts = [c[0] for c in clock]

    def sim_of(ts_us):
        i = bisect.bisect_right(cts, ts_us) - 1
        if i < 0:
            return clock[0][1] - (clock[0][0] - ts_us) / 1e6
        return clock[i][1] + (ts_us - clock[i][0]) / 1e6

    m = lambda s: s / 60.0
    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(7.2, 4.6),
                                   gridspec_kw={"height_ratios": [1, 1.3], "hspace": 0.08})
    fig.patch.set_facecolor(SURFACE)
    for ax in (ax1, ax2):
        ax.set_facecolor(SURFACE)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color(MUTED)
        ax.tick_params(colors=INK2, labelsize=8)
        ax.grid(axis="y", color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)

    # Eclipse shading from the BATTERY state field.
    start = None
    for s, _, st in pts:
        if st == "ECLIPSE" and start is None:
            start = s
        elif st == "SUNLIT" and start is not None:
            for ax in (ax1, ax2):
                ax.axvspan(m(start), m(s), color=SHADE, lw=0, zorder=0)
            start = None
    if start is not None:
        for ax in (ax1, ax2):
            ax.axvspan(m(start), m(pts[-1][0]), color=SHADE, lw=0, zorder=0)

    ax1.plot([m(p[0]) for p in pts], [p[1] for p in pts], color=INK, lw=1.5)
    ax1.axhline(margin, color=INK2, lw=1, ls=(0, (4, 3)))
    ax1.text(m(pts[0][0]), margin, f" payload margin {margin:.1f} Wh", va="bottom", ha="left",
             fontsize=7.5, color=INK2)
    ax1.set_ylabel("Battery (Wh)", fontsize=8.5, color=INK2)

    # Halts: SCHED_HALT ... SCHED_RESUME, wall-clock, mapped to sim time.
    open_h = None
    for e in sorted(evs, key=lambda e: f(e.get("ts_us")) or 0):
        if e["tag"] == "SCHED_HALT" and open_h is None:
            open_h = f(e["ts_us"])
        elif e["tag"] == "SCHED_RESUME" and open_h is not None:
            ax2.axvspan(m(sim_of(open_h)), m(sim_of(f(e["ts_us"]))), color="#f6dfd3", lw=0, zorder=0)
            open_h = None

    top = max([f(b.get("latency_us")) / 1e6 for b in bts if f(b.get("latency_us"))] + [400])
    for cls, col in (("routine", ROUTINE), ("urgent", URGENT)):
        done = [b for b in bts if b.get("cls") == cls and b.get("completed") == "1"]
        miss = [b for b in bts if b.get("cls") == cls and b.get("completed") != "1" and b.get("dropped") != "1"]
        ax2.scatter([m(f(b.get("arrival_sim_t_s") or b["mark_sim_t_s"])) for b in done], [f(b["latency_us"]) / 1e6 for b in done],
                    s=14, color=col, edgecolor=SURFACE, linewidth=0.5, label=f"{cls} ({len(done)})", zorder=3)
        if miss:
            ax2.scatter([m(f(b.get("arrival_sim_t_s") or b["mark_sim_t_s"])) for b in miss], [top * 1.3] * len(miss), s=14,
                        facecolor="none", edgecolor=col, linewidth=1, label=f"{cls} unfinished ({len(miss)})",
                        zorder=3)
        slo = next((f(b.get("slo_s")) for b in bts if b.get("cls") == cls and b.get("slo_s")), None)
        if slo:
            ax2.axhline(slo, color=INK2, lw=0.8, ls=(0, (4, 3)))
            ax2.text(m(pts[-1][0]), slo, f"{cls} SLO {slo:.0f} s ",
                     ha="right", va="bottom", fontsize=7, color=INK2)
    ax2.set_yscale("log")
    ax2.set_ylabel("Frame latency (s)", fontsize=8.5, color=INK2)
    ax2.set_xlabel("Simulated time (min)", fontsize=8.5, color=INK2)
    leg = ax2.legend(loc="upper center", fontsize=7.5, frameon=False, ncol=2)
    for t in leg.get_texts():
        t.set_color(INK)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    fig.savefig(a.out, bbox_inches="tight", facecolor=SURFACE)
    if a.out.endswith(".pdf"):
        fig.savefig(a.out[:-4] + ".png", bbox_inches="tight", facecolor=SURFACE, dpi=200)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
