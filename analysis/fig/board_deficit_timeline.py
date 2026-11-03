#!/usr/bin/env python3
"""Board-measured energy-deficit timeline (advisor slides, 2026-10-20).

Columns: static high at 20 s / 5 min, SIPP at 20 s / 5 min, SIPP at 5 min / 6 h,
all 1x clear sky on the dawn-dusk 3.7 W platform (block D of the board grid).
Rows: battery above the survival floor (daemon BATTERY records) and effort share
of the three adaptive stages per 2-min bin (EXEC records). Time is the battery
model's time (sim_t), so the fast-forwarded gaps between bursts appear at true length.

    python3 analysis/fig/board_deficit_timeline.py -> analysis/fig/board_deficit_timeline.{png,pdf}
"""
import csv, gzip, os, re, sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RUNS = os.path.join(ROOT, "runs/board-grid")
CSV = os.path.join(ROOT, "analysis/board-grid/all/runs.csv")
PANELS = [("E37P2C3-high-s1", "Static high, 20 s / 5 min"),
          ("E37P2C3-final-s1", "SIPP, 20 s / 5 min"),
          ("E37P4C3-final-s1", "SIPP, 5 min / 6 h")]
EFFORT = {2: "low", 5: "low", 15: "low", 3: "med", 18: "med", 16: "med", 4: "high", 6: "high", 17: "high"}
COLORS = {"low": "#c6d6ee", "med": "#6c9bd2", "high": "#1f4e8c"}
KV = re.compile(r"(\w+)=(\S+)")
BIN_S = 120.0


def parse(run):
    path = os.path.join(RUNS, run, "console.log.gz")
    if not os.path.exists(path):
        sys.exit(f"missing {path}")
    cfg, tmap, batt, execs, waits, ingest, done = None, [], [], [], 0, {}, {}
    with gzip.open(path, "rt", errors="replace") as f:
        for line in f:
            tag = line.split(" ", 1)[0]
            if tag not in ("BATTERY_CONFIG", "BATTERY", "BATTERY_FF", "EXEC", "SCHED_ENERGY_WAIT", "INGEST"):
                continue
            kv = dict(KV.findall(line))
            if tag == "BATTERY_CONFIG":
                cfg = kv
            elif tag == "BATTERY":
                ts, t = int(kv["ts_us"]), float(kv["sim_t_s"])
                tmap.append((ts, t))
                batt.append((t, float(kv["wh"])))
            elif tag == "BATTERY_FF":
                ts = int(kv["ts_us"])
                tmap += [(ts, float(kv["sim_t_before_s"])), (ts + 1, float(kv["sim_t_after_s"]))]
                batt += [(float(kv["sim_t_before_s"]), float(kv["wh_before"])),
                         (float(kv["sim_t_after_s"]), float(kv["wh_after"]))]
            elif tag == "INGEST":
                ingest[kv["uuid"]] = (int(kv["ts_us"]), int(kv["capture_us"]), int(kv["deadline_us"]) - int(kv["capture_us"]))
            elif tag == "EXEC":
                ts = int(kv["ts_us"])  # logged when the module ends
                done[kv["uuid"]] = max(done.get(kv["uuid"], 0), ts)
                pid = int(kv["param_id"])
                if pid in EFFORT:
                    execs.append((ts, EFFORT[pid]))
            else:
                waits += 1
    if cfg is None or not ingest:
        sys.exit(f"{run}: no BATTERY_CONFIG or INGEST records")
    floor = float(cfg["survival_orbits"]) * float(cfg["bus_w"]) * float(cfg["orbit_min"]) / 60.0
    tmap.sort()
    ts_arr, t_arr = np.array([a for a, _ in tmap], float), np.array([b for _, b in tmap])
    sim = lambda ts: float(np.interp(ts, ts_arr, t_arr))
    ex = [(sim(ts), e) for ts, e in execs]
    w_min = min(w for _, _, w in ingest.values())
    frames = []  # (completion sim t, latency s, urgent, window s); board clock for latency, as in runs.csv
    for u, (ts_in, cap, w) in ingest.items():
        if u in done:
            frames.append((sim(done[u]), (done[u] - cap) / 1e6, w < 2 * w_min, w / 1e6))
    arr = sorted(sim(ts) for ts, _, _ in ingest.values())
    bursts, start = [], arr[0]
    for a, b in zip(arr, arr[1:]):
        if b - a > 120.0:
            bursts.append((start, a)); start = b
    bursts.append((start, arr[-1]))
    batt.sort()
    return floor, batt, ex, waits, frames, bursts


def main():
    stats = {r["run"]: r for r in csv.DictReader(open(CSV))}
    parsed = [parse(r) for r, _ in PANELS]
    tmax = max(max(t for t, _ in p[1]) for p in parsed) / 60.0
    fig, axes = plt.subplots(3, 3, figsize=(13, 7.4), sharex=True, sharey="row",
                             gridspec_kw={"height_ratios": [1.3, 1, 0.8]})
    for col, ((run, title), (floor, batt, ex, waits, frames, bursts)) in enumerate(zip(PANELS, parsed)):
        s = stats[run]
        late = int(s["late"]) + int(s["dropped"]) + int(s["unfinished"])
        for row in range(3):
            for b0, b1 in bursts:
                axes[row, col].axvspan(b0 / 60.0, b1 / 60.0, color="#eef2f7", lw=0, zorder=0)
        ax = axes[0, col]
        for urg, color, size in ((False, "#2f6fc4", 4), (True, "#e8692c", 12)):
            pts = [(t / 60.0, l) for t, l, u, _ in frames if u == urg]
            ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=size, color=color, lw=0,
                       label="urgent" if urg else "routine", zorder=3)
        for wdw in sorted({round(w) for _, _, _, w in frames}):
            ax.axhline(wdw, color="#555", lw=0.8, ls="--", zorder=2)
            ax.text(tmax * 0.995, wdw * 1.12, f"deadline {wdw:.0f} s" if wdw < 3600 else f"deadline {wdw / 3600:.0f} h",
                    ha="right", va="bottom", fontsize=7.5, color="#555")
        ax.set_yscale("log")
        ax.set_ylim(1, 5e4)
        ax.set_title(f"{title}\nU = {float(s['U']):.3f}, urgent hit {float(s['urgent_hit']):.2f}, "
                     f"{late} of {s['frames']} late or unfinished\n"
                     + (f"{waits:,} planned waits for energy (seconds each)" if waits else "stalls at the floor, no plan"),
                     fontsize=10, loc="left")
        ax1 = axes[1, col]
        t = np.array([a for a, _ in batt]) / 60.0
        w = np.array([b for _, b in batt]) - floor
        ax1.plot(t, w, color="black", lw=1.2, zorder=3)
        ax1.axhline(0, color="#c0392b", lw=0.8, ls=":")
        ax2 = axes[2, col]
        nb = int(np.ceil(tmax * 60 / BIN_S))
        cnt = {e: np.zeros(nb) for e in COLORS}
        for tt, e in ex:
            cnt[e][min(int(tt // BIN_S), nb - 1)] += 1
        tot = sum(cnt.values())
        x = np.arange(nb) * BIN_S / 60.0
        bottom = np.zeros(nb)
        for e in ("low", "med", "high"):
            share = np.divide(cnt[e], tot, out=np.zeros(nb), where=tot > 0)
            ax2.bar(x, share, width=BIN_S / 60.0, bottom=bottom, align="edge", color=COLORS[e],
                    label=e, lw=0, zorder=3)
            bottom += share
        ax2.set_xlabel("time (min)")
        for a in (ax, ax1, ax2):
            for sp in ("top", "right"):
                a.spines[sp].set_visible(False)
    axes[0, 0].set_ylabel("latency (s)")
    axes[1, 0].set_ylabel("battery above\nfloor (Wh)")
    axes[2, 0].set_ylabel("effort share")
    axes[2, 0].set_xlim(0, tmax)
    h0, l0 = axes[0, 0].get_legend_handles_labels()
    h2, l2 = axes[2, 0].get_legend_handles_labels()
    import matplotlib.patches as mp
    hb = mp.Patch(color="#eef2f7", label="frames arriving (burst)")
    fig.legend(h0 + h2 + [hb], l0 + l2 + [hb.get_label()], loc="lower center", ncol=6, frameon=False,
               markerscale=2)
    fig.text(0.99, 0.005, "Board-measured, 1x clear sky, 3.7 W payload power, start 0.05 Wh above floor. "
             "Latency = capture to last module end, at completion time; unshaded = gap between bursts (no frames).",
             ha="right", fontsize=7.5, color="#666")
    fig.tight_layout(rect=(0, 0.045, 1, 1))
    out = os.path.join(HERE, "board_deficit_timeline")
    fig.savefig(out + ".png", dpi=200)
    fig.savefig(out + ".pdf")
    print(out + ".png")
    for (run, _), p in zip(PANELS, parsed):
        fr = p[4]
        hit = lambda u: sum(1 for _, l, uu, w in fr if uu == u and l <= w) / max(1, sum(1 for f in fr if f[2] == u))
        print(run, "bursts(min)", [(round(a / 60, 1), round(b / 60, 1)) for a, b in p[5]],
              "urgent hit", round(hit(True), 3), "routine hit", round(hit(False), 3),
              "urgent max", round(max(l for _, l, u, _ in fr if u), 2))


if __name__ == "__main__":
    main()
