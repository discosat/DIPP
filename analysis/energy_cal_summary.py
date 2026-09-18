#!/usr/bin/env python3
"""Per-stage energy from the calibration runs, with the I/O term isolated.

    analysis/energy_cal_summary.py            # -> stage4.5-models/stage_energy.json
    analysis/energy_cal_summary.py --enc      # -> stage4.5-models/encoder_energy.json

The encoder (tools/phase3_enc_energy.sh, energy_enc/) cannot have a matched
baseline -- it uploads ~0.3 MB where the tile stage uploads 15 MB -- so it is
differenced against the fixed stages from stage_energy.json and charged the
residual upload from the I/O term, pro rata by bytes.

Every run in energy_cal2/ is one workload at n=50 frames with its own idle
floor read immediately before it (tools/energy_calibration2.sh, 3.sh). The
idle-corrected work of a run is E_run - P_idle * T_run; per frame, /n.

The subtraction chain, all per 99-tile frame:
  I/O        = seg_low_keepall - seg_low_keepnone      (same model, 15 MB vs 0 uploaded)
  fixed      = baseline - I/O                          (demosaic + tile, no upload)
  cls_x      = cls_x(class 10, uploads nothing) - fixed
  seg_low    = seg_low_keepnone - fixed
  seg_med/hi = seg_x_keepall - baseline                (both upload 15 MB)

Why the floor rule. The floor is bimodal after a boot: 3.0-3.3 W for up to
~45 min, then 2.75 W. A run measured while the floor shifts is off by up to
0.35 W, i.e. up to ~+-300 uWh/frame. A run is VALID if its floor was read
before and after and the two agree within DRIFT (the bracket), or -- for runs
without an after-reading -- if its single floor lies in the settled band. A
rep is used only if every run in its chain is valid (rep 1 is not: its floors
run 3.30 -> 3.08 -> 2.76 across the chain).
"""
import glob, json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "stage4.5-models", "energy_cal2")
SETTLED = (2.70, 2.85)
DRIFT = 0.03                     # W; ~25 uWh/frame over a 150 s run
CHAIN = ["seg_low_keepnone", "seg_low_keepall", "baseline", "cls_low", "cls_medop",
         "cls_high", "seg_med_keepall", "seg_high_keepall"]


def load(path):
    d = json.load(open(path))
    idle = d["idle"]["energy"]["avg_power_w"]
    v = [x for k, x in d.items() if k != "idle"][0]
    e = v["energy"]; n = len(v["classify_us"]) or len(v["demosaic_us"])
    lat = sorted(v["classify_us"] or v["demosaic_us"])
    after = (v.get("idle_after") or {}).get("avg_power_w")
    if after is not None:
        # a bracket that stays inside the settled band is a settled run even if
        # the board is a little warmer after a heavy one (+0.04 W after 6 min
        # of segmentation-high); a bracket that crosses a state is not
        both = all(SETTLED[0] <= x <= SETTLED[1] for x in (idle, after))
        valid = both or abs(after - idle) <= DRIFT
        idle = (idle + after) / 2                       # bracketed: use the mean
    else:
        valid = SETTLED[0] <= idle <= SETTLED[1]
    work = (e["energy_wh"] - idle * e["measured_s"] / 3600) * 1e6
    return dict(idle=idle, after=after, valid=valid and n > 0, n=n, per=work / n if n else float("nan"),
                exec_ms=lat[n // 2] / 1000 if n else float("nan"), T=e["measured_s"])


def enc():
    E = json.load(open(os.path.join(HERE, "stage4.5-models", "stage_energy.json")))
    C = json.load(open(os.path.join(HERE, "stage4.5-models", "encoder_corpus.json")))
    # --enc-dir energy_enc_matched: the encoder measured on the matched frame
    # (clear water, the content it sees in flight); J2K is 32% faster there
    # than on a random frame, so that set is what the hints carry.
    ed = sys.argv[sys.argv.index("--enc-dir") + 1] if "--enc-dir" in sys.argv else "energy_enc"
    ED = os.path.join(HERE, "stage4.5-models", ed)
    io_per_mb = E["io_15mb"]["uwh"] / 15.0
    out = {}
    # quality: one-frame-lost PSNR on the 300 corpus tiles at the 3 kB budget
    deg = {}
    for tile in C["j2k"]:
        for r in tile:
            if r["budget"] == 3000:
                deg.setdefault(r["layers"], []).append(r["deg1"])
    print(f"{'rung':8} {'floor W':>8} {'n':>3} {'ms/frame':>9} {'B/tile':>7} {'run uWh':>8} {'enc uWh':>8} {'PSNR@1lost':>10}")
    for name, path in (("L1", "j2k_49.22"), ("L2", "j2k_98.2"), ("L3", "j2k_147.56"), ("JXL_e3", "jxl_e3"), ("JXL_e5", "jxl_e5")):
        f = os.path.join(ED, path + ".json")
        if not os.path.exists(f):
            print(f"{name:8} (not measured yet)"); continue
        d = json.load(open(f)); x = load(f)
        v = [y for k, y in d.items() if k != "idle"][0]
        mb = v.get("encoded_mean_bytes", 3000) * 99 / 2**20
        uwh = x["per"] - E["fixed"]["uwh"] - io_per_mb * mb
        psnr = float(np.nanmean(deg[int(name[1])])) if name.startswith("L") else None
        out[name] = dict(ms=x["exec_ms"], uwh=uwh, run_uwh=x["per"], floor=x["idle"], n=x["n"],
                         bytes_per_tile=v.get("encoded_mean_bytes"), psnr_1lost=psnr)
        print(f"{name:8} {x['idle']:8.4f} {x['n']:3} {x['exec_ms']:9.1f} {v.get('encoded_mean_bytes', 0):7.0f} {x['per']:8.1f} {uwh:8.1f} {psnr if psnr is None else round(psnr, 2)!s:>10}")
    bf = os.path.join(ED, "baseline.json")
    if os.path.exists(bf):
        b = load(bf)
        print(f"same-session baseline (dem+tile+15 MB): {b['per']:.1f} uWh/frame at floor {b['idle']:.4f} "
              f"(calibration: {E['fixed']['uwh'] + E['io_15mb']['uwh']:.1f})")
        out["_baseline"] = b
    out["_meta"] = dict(unit="uWh per 99-tile frame, encoder only", io_uwh_per_mb=io_per_mb)
    out["_meta"]["source_dir"] = ed
    p = os.path.join(HERE, "stage4.5-models", "encoder_energy.json" if ed == "energy_enc" else f"encoder_energy_{ed.split('_')[-1]}.json")
    json.dump(out, open(p, "w"), indent=1); print(f"wrote {p}")


def main():
    if "--enc" in sys.argv:
        return enc()
    runs = {}
    for f in sorted(glob.glob(os.path.join(D, "*.json"))):
        k = os.path.basename(f)[:-5]; name, rep = k.rsplit("_r", 1)
        runs.setdefault(int(rep), {})[name] = load(f)
    per_rep = {}
    print(f"{'rep':>3} {'run':20} {'floor W':>22} {'n':>3} {'uWh/frame':>10} {'exec ms':>8}")
    for rep in sorted(runs):
        r = runs[rep]
        ok = all(name in r and r[name]["valid"] for name in CHAIN)
        for name in CHAIN:
            x = r.get(name)
            if x:
                fl = f"{x['idle']:8.4f}" + (f" (after {x['after']:.3f})" if x["after"] else "")
                print(f"{rep:3} {name:20} {fl:>22} {x['n']:3} {x['per']:10.1f} {x['exec_ms']:8.1f}"
                      + ("" if x["valid"] else "   INVALID floor"))
        print(f"    rep {rep}: {'all runs valid, used' if ok else 'invalid or missing runs: not used'}")
        if not ok:
            continue
        p = {k: v["per"] for k, v in r.items()}
        io = p["seg_low_keepall"] - p["seg_low_keepnone"]
        fixed = p["baseline"] - io
        per_rep[rep] = dict(
            io_15mb=io, fixed=fixed,
            cls_low=p["cls_low"] - fixed, cls_med=p["cls_medop"] - fixed, cls_high=p["cls_high"] - fixed,
            seg_low=p["seg_low_keepnone"] - fixed,
            seg_med=p["seg_med_keepall"] - p["baseline"], seg_high=p["seg_high_keepall"] - p["baseline"])
    if not per_rep:
        sys.exit("no settled rep")
    keys = list(next(iter(per_rep.values())))
    out = {k: dict(uwh=float(np.mean([per_rep[r][k] for r in per_rep])),
                   reps=[float(per_rep[r][k]) for r in per_rep]) for k in keys}
    # 2026-09-17: the demosaic module's rendering became one pass (and tile
    # takes 8-bit input), so the fixed stages are re-measured in
    # demosaic_onepass/baseline*.json -- baseline runs of the NEW modules,
    # differenced against the I/O term. The classification and segmentation
    # terms are unaffected: each was a difference within its own session.
    OP = os.path.join(HERE, "stage4.5-models", "demosaic_onepass")
    bs = sorted(glob.glob(os.path.join(OP, "baseline*.json")))
    if bs:
        vals, ms = [], []
        for f in bs:
            x = load(f)
            if x["valid"]:
                vals.append(x["per"] - out["io_15mb"]["uwh"]); ms.append(x["exec_ms"])
                print(f"  one-pass baseline {os.path.basename(f):26} n={x['n']:2} floor {x['idle']:.4f}  fixed {vals[-1]:6.1f} uWh  demosaic {x['exec_ms']:.0f} ms")
        if vals:
            out["fixed_old_float_path"] = out["fixed"]
            out["fixed"] = dict(uwh=float(np.mean(vals)), reps=[float(v) for v in vals],
                                note="one-pass demosaic (2026-09-17), baseline - I/O")
            out["_exec_ms"] = {}
            out["_exec_ms"]["demosaic"] = float(np.median(ms))
    out["_meta"] = dict(settled_reps=sorted(per_rep), floor_rule=SETTLED, unit="uWh per 99-tile frame",
                        note="cls/seg per full frame; downstream stages see only retained tiles")
    exec_ms = {}
    for rep in per_rep:
        for name, x in runs[rep].items():
            exec_ms.setdefault(name, []).append(x["exec_ms"])
    out["_exec_ms"] = {**out.get("_exec_ms", {}), **{k: float(np.median(v)) for k, v in exec_ms.items()}}
    print(f"\nderived, mean over settled reps {sorted(per_rep)} (uWh per frame):")
    for k in keys:
        print(f"  {k:10} {out[k]['uwh']:8.1f}   reps " + "  ".join(f"{v:7.1f}" for v in out[k]["reps"]))
    p = os.path.join(HERE, "stage4.5-models", "stage_energy.json")
    json.dump(out, open(p, "w"), indent=1); print(f"wrote {p}")


if __name__ == "__main__":
    sys.exit(main())
