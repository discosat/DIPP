#!/usr/bin/env python3
"""Per-execution cost of encoder configs from tools/phase3_enc_board.sh output.

    analysis/enc_board_summary.py analysis/stage4.5-models/dev_enc_g050.json --n 4 --rep 3

Idle-corrected work energy, E_work = (E_run - P_idle*T_run) - (E_base -
P_idle*T_base), divided by the batches per launch; median EXEC latency; mean
encoded bytes per tile (the board codec's own output, to cross-check the host
sweep). With --rep > 1 the _r2.._rN files are read too and the spread between
independent launches is reported.
"""
import argparse, json, os, sys
import numpy as np


def load(path):
    d = json.load(open(path))
    idle = d["idle"]["energy"]["avg_power_w"]; b = d["baseline"]
    def work(v):
        er, tr = v["energy"]["energy_wh"], v["energy"]["measured_s"]
        eb, tb = b["energy"]["energy_wh"], b["energy"]["measured_s"]
        return (er - idle * tr / 3600) - (eb - idle * tb / 3600)
    rows = {}
    for k, v in d.items():
        if k in ("idle", "baseline") or not isinstance(v, dict) or "classify_us" not in v \
                or not (v.get("energy") or {}).get("energy_wh"):
            continue
        lat = sorted(v["classify_us"])
        rows[k] = dict(exec_ms=lat[len(lat) // 2] / 1000 if lat else float("nan"),
                       n_exec=len(lat), bytes=v.get("encoded_mean_bytes", float("nan")),
                       work_wh=work(v))
    return idle, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json"); ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--rep", type=int, default=1)
    a = ap.parse_args()
    files = [a.json] + [a.json.replace(".json", f"_r{r}.json") for r in range(2, a.rep + 1)]
    files = [f for f in files if os.path.exists(f)]
    per = [load(f) for f in files]
    print(f"idle {per[0][0]:.4f} W   launches: {len(files)}   batches/launch: {a.n}")
    print(f"{'config':22} {'EXEC ms':>9} {'ms/tile':>8} {'B/tile':>7} {'uWh/exec':>10} {'+-':>6}")
    keys = list(per[0][1].keys())
    out = {}
    for k in keys:
        rs = [p[1][k] for p in per if k in p[1]]
        ms = np.median([r["exec_ms"] for r in rs]); by = np.mean([r["bytes"] for r in rs])
        e = np.array([r["work_wh"] * 1e6 / a.n for r in rs])
        out[k] = dict(exec_ms=float(ms), bytes=float(by), uwh=float(e.mean()),
                      uwh_sd=float(e.std(ddof=1)) if len(e) > 1 else None, launches=len(rs))
        sd = f"{e.std(ddof=1):6.0f}" if len(e) > 1 else "     -"
        print(f"{k:22} {ms:9.1f} {ms/99:8.2f} {by:7.0f} {e.mean():10.1f} {sd}")
    json.dump(out, open(a.json.replace(".json", "_summary.json"), "w"), indent=1)


if __name__ == "__main__":
    sys.exit(main())
