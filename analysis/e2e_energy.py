#!/usr/bin/env python3
"""Directly measured per-frame energy of the static pipelines, against the
composition from per-stage calibration.

    analysis/e2e_energy.py analysis/stage4.5-models/e2e_static50.json

Each run is one static pipeline (2 low, 6 medium, 3 high) over n frames of the
same CloudSEN12+ scene with its own idle floor read immediately before
(tools/phase4_e2e.py --idle). Per frame: (E_run - P_idle T_run) / n. The
composed prediction is analysis/value_of_effort.py's NORMAL view for that
scene's own retention (the classifier's decisions on this frame, from the run),
so agreement checks the calibration chain end to end: fixed + cls + seg x kept
+ enc x uploaded, with the I/O term for what was actually uploaded.
"""
import json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
S = os.path.join(HERE, "stage4.5-models")
PIPE = {2: ("low", "low", "L1"), 6: ("med", "med", "L2"), 3: ("high", "high", "L3")}
CLS_KEY = {"low": "cls_low", "med": "cls_med", "high": "cls_high"}


def main():
    runs = json.load(open(sys.argv[1]))
    E = json.load(open(os.path.join(S, "stage_energy.json")))
    ENC = json.load(open(os.path.join(S, "encoder_energy.json")))
    io_per_mb = E["io_15mb"]["uwh"] / 15.0
    out = {}
    print(f"{'run':36} {'floor W':>8} {'n':>3} {'measured':>9} {'composed':>9} {'diff':>7}   kept/frame  uploaded/frame  ms/frame")
    for key, r in runs.items():
        e, fl = r["energy"], r.get("idle") or {}
        if not e or not fl:
            print(f"{key:36} no energy/floor"); continue
        batches = sum(1 for m, _ in r["execs"] if "demosaic" in m)
        floor, after = fl["avg_power_w"], (r.get("idle_after") or {}).get("avg_power_w")
        drift = "" if after is None else (f"  floor after {after:.4f}" + ("  DRIFT" if abs(after - floor) > 0.03 else ""))
        if after is not None:
            floor = (floor + after) / 2
        per = (e["energy_wh"] - floor * e["measured_s"] / 3600) * 1e6 / batches
        c, s, enc = PIPE[r["pipeline"]]
        kept = sum(1 for m, _ in r["execs"] if "segment" in m) / batches            # seg EXEC only when cls kept >0
        n_kept = len(r.get("seg_kept") or []) / batches                              # tiles seen by segmentation
        n_up = r["uploaded"] / batches
        enc_mb = n_up * (ENC[enc]["bytes_per_tile"] or 3000) / 2**20
        composed = (E["fixed"]["uwh"] + E[CLS_KEY[c]]["uwh"] + E[f"seg_{s}"]["uwh"] * n_kept / 99
                    + ENC[enc]["uwh"] * n_up / 99 + io_per_mb * enc_mb)
        lat = {}
        for m, us in r["execs"]:
            lat.setdefault(m, []).append(us)
        ms = sum(sorted(v)[len(v) // 2] * len(v) / batches for v in lat.values()) / 1000
        out[key] = dict(pipeline=r["pipeline"], n=batches, floor_w=floor, floor_after=after, measured_uwh=per,
                        composed_uwh=composed, kept_per_frame=n_kept, uploaded_per_frame=n_up, ms_per_frame=ms)
        print(f"{key:36} {fl['avg_power_w']:8.4f} {batches:3} {per:9.1f} {composed:9.1f} {per-composed:+7.1f}   "
              f"{n_kept:6.1f}      {n_up:6.1f}        {ms:8.0f}{drift}")
    p = sys.argv[1].replace(".json", "_energy.json")
    json.dump(out, open(p, "w"), indent=1); print(f"wrote {p}")


if __name__ == "__main__":
    sys.exit(main())
