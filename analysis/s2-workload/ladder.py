#!/usr/bin/env python3
"""The region ladder (W9): how much imaging a sun-synchronous imager does over
growing areas of interest, one satellite vs the constellation, in DISCO frames.

    python3 analysis/s2-workload/ladder.py --frame-period-s 6.68 --merge-gap-s 600 \\
        -o analysis/s2-workload/ladder.csv east_greenland greenland arctic global_land

Per region directory (tools/s2_acquisition.py output): windows.json and
characterisation.json (whose full_days bound the statistics). Frames are
counted as make_arrival_trace.py places them: one per --frame-period-s along
each window, at least one per window. Bursts merge windows closer than
--merge-gap-s, per satellite set.
"""
import argparse, csv, datetime as dt, json, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def frames_in(w, period):
    dur = (w["b"] - w["a"]).total_seconds()
    return int(dur // period) + 1


def stats(ws, days, period, gap):
    ws = sorted(ws, key=lambda w: w["a"])
    bursts = []
    for w in ws:
        if bursts and (w["a"] - bursts[-1]["b"]).total_seconds() < gap:
            bursts[-1]["b"] = max(bursts[-1]["b"], w["b"]); bursts[-1]["fr"] += frames_in(w, period)
        else:
            bursts.append({"a": w["a"], "b": w["b"], "fr": frames_in(w, period)})
    lo, hi = days[0], days[-1] + dt.timedelta(days=1)
    bs = [b for b in bursts if lo <= b["a"] < hi]
    nd = len(days)
    fr = sorted(b["fr"] for b in bs)
    gaps = [(n["a"] - p["b"]).total_seconds() / 60 for p, n in zip(bs, bs[1:])]

    def pct(v, p):
        return v[min(len(v) - 1, int(round(p * (len(v) - 1))))] if v else None
    return {"bursts_per_day": round(len(bs) / nd, 1), "frames_per_day": round(sum(fr) / nd),
            "burst_frames_median": statistics.median(fr) if fr else None, "burst_frames_p90": pct(fr, 0.9),
            "burst_frames_max": fr[-1] if fr else None,
            "gap_min_median": round(statistics.median(gaps), 1) if gaps else None, "days": nd}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("regions", nargs="+")
    ap.add_argument("--frame-period-s", type=float, required=True)
    ap.add_argument("--merge-gap-s", type=float, required=True)
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    rows = []
    for r in a.regions:
        d = os.path.join(HERE, r)
        ch = json.load(open(os.path.join(d, "characterisation.json")))
        days = [dt.datetime.fromisoformat(x).replace(tzinfo=dt.timezone.utc) for x in ch["full_days"]]
        ws = json.load(open(os.path.join(d, "windows.json")))
        for w in ws:
            w["a"] = dt.datetime.fromisoformat(w["t0"]); w["b"] = dt.datetime.fromisoformat(w["t1"])
        for sats, label in ((("S2A",), "S2A"), (("S2A", "S2B", "S2C"), "S2A+B+C")):
            s = stats([w for w in ws if w["satellite"] in sats], days, a.frame_period_s, a.merge_gap_s)
            rows.append({"region": r, "satellites": label, **s})
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print(f"{'region':16} {'sats':8} {'bursts/d':>8} {'frames/d':>8} {'burst med/p90/max':>18} {'gap med':>8}")
    for x in rows:
        print(f"{x['region']:16} {x['satellites']:8} {x['bursts_per_day']:>8} {x['frames_per_day']:>8} "
              f"{str(x['burst_frames_median']) + '/' + str(x['burst_frames_p90']) + '/' + str(x['burst_frames_max']):>18} "
              f"{x['gap_min_median']:>7} m")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
