#!/usr/bin/env python3
"""Fit the size-aware cost model from the size sweep; write cost_model.json.

    analysis/cost_model_fit.py analysis/size-sweep -o analysis/cost_model.json \\
        --manifest experiments/board/flight-config/manifest.yaml

Input: the per-run JSON files tools/size_sweep.py writes (only complete,
valid runs are used; the rest are listed). Every execution carries its slot
(param_id + 1), its input size n_input and its measured latency.

Latency, per slot:
  points  the median latency at every distinct n that was measured, with its
          count -- these are what the store answers with at that n, exactly
          as measured, however the curve bends;
  model   OLS over the point medians weighted by their counts, the intercept
          re-expressed at n_ref (1 for the frame-level stages -- demosaic,
          tile, the monolith -- 99 for the tile-level ones). Refused for a
          slot with < 3 distinct n or a negative slope; its points are still
          seeded. Non-linearity is REPORTED, not assumed away: r2 and the
          95th-percentile relative residual of the points against the line.

Energy (the user's decision, 2026-09-28): an execution is charged its
configuration's mean power above idle times its latency. Mean power per
configuration is the calibrated flight hint, energy_cost / latency_cost
(Stage 4.5 energy calibration, per-run idle floors; analysis/stage4.5-models/
README.md), so point and model energies are P x latency.

Validation of that premise (the plan's U3: is a stage's mean power flat in
its input size?): for each sweep run, the measured whole-board energy above
the idle floor (mean of the floors before and after) against the predicted
sum of P x latency over the run's executions. The ratio is not expected to be
1 -- ingest, file writes and process start-up are not inside module latency --
but it must not drift with input size; the per-batch excess is reported too.
"""
import argparse, glob, json, math, os, statistics, sys
from collections import defaultdict
from pathlib import Path

import yaml

FRAME_LEVEL = {"libdemosaic", "libtile", "libsingle_module"}


def load_runs(d):
    ok, skipped = [], []
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            r = json.load(open(p))
        except Exception:
            skipped.append((os.path.basename(p), "unreadable"))
            continue
        if "label" not in r or "spec" not in r:
            continue
        if not r.get("complete"):
            skipped.append((r["label"], f"incomplete ({r.get('status')})"))
        elif int(r.get("n_completed") or 0) < int(r["spec"]["n"]):
            skipped.append((r["label"], f"completed {r.get('n_completed')} of {r['spec']['n']} batches (cut short)"))
        elif r.get("valid") is False:
            skipped.append((r["label"], f"invalid floors ({r.get('floor_before_w')} -> {r.get('floor_after_w')})"))
        else:
            ok.append(r)
    return ok, skipped


def hints(manifest):
    man = yaml.safe_load(Path(manifest).read_text())
    out = {}
    for slot, rel in man["modules"].items():
        doc = yaml.safe_load((Path(manifest).parent / rel).read_text())
        lat, en = int(doc["latency_cost"]), float(doc["energy_cost"])
        # W = Wh / h: (en * 1e-6) / (lat * 1e-6 / 3600)
        out[int(slot)] = {"lat_us": lat, "en_uwh": en, "power_w": 3600.0 * en / lat if lat else None}
    return out


def wls(xs, ys, ws):
    sw = sum(ws)
    mx = sum(w * x for x, w in zip(xs, ws)) / sw
    my = sum(w * y for y, w in zip(ys, ws)) / sw
    sxx = sum(w * (x - mx) ** 2 for x, w in zip(xs, ws))
    if sxx == 0:
        return None
    b = sum(w * (x - mx) * (y - my) for x, y, w in zip(xs, ys, ws)) / sxx
    a = my - b * mx
    ss_res = sum(w * (y - (a + b * x)) ** 2 for x, y, w in zip(xs, ys, ws))
    ss_tot = sum(w * (y - my) ** 2 for y, w in zip(ys, ws))
    return a, b, (1 - ss_res / ss_tot) if ss_tot else 1.0


def p95(v):
    v = sorted(v)
    return v[min(len(v) - 1, int(math.ceil(0.95 * len(v))) - 1)] if v else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sweep_dir")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    runs, skipped = load_runs(a.sweep_dir)
    if not runs:
        raise SystemExit(f"no complete, valid runs in {a.sweep_dir}")
    hint = hints(a.manifest)

    per = defaultdict(lambda: defaultdict(list))     # slot -> n -> [lat_us]
    first = defaultdict(list)                         # slot -> [(first, median rest)]
    module_of = {}
    for r in runs:
        by_slot = defaultdict(list)
        for x in r["execs"]:
            slot = x["param_id"] + 1
            module_of[slot] = x["module"]
            per[slot][x["n_input"]].append(x["latency_us"])
            by_slot[slot].append(x)
        for slot, xs in by_slot.items():
            xs.sort(key=lambda e: e["ts_us"])
            if len(xs) >= 3 and len({e["n_input"] for e in xs}) == 1:
                first[slot].append(xs[0]["latency_us"] / statistics.median(e["latency_us"] for e in xs[1:]))

    out = {"slots": {}, "_meta": {
        "sweep_dir": a.sweep_dir, "runs_used": len(runs), "runs_skipped": skipped,
        "manifest": a.manifest, "energy": "configuration mean power (calibrated hint) x latency"}}
    report = []
    for slot in sorted(per):
        module = module_of[slot]
        h = hint.get(slot)
        if h is None or h["power_w"] is None:
            raise SystemExit(f"slot {slot} has no hint in {a.manifest}")
        P = h["power_w"]
        n_ref = 1 if module in FRAME_LEVEL else 99
        pts = []
        for n in sorted(per[slot]):
            lat = statistics.median(per[slot][n])
            pts.append({"n": n, "lat_us": int(round(lat)), "en_uwh": round(P * lat / 3600.0, 3),
                        "n_exec": len(per[slot][n])})
        xs = [p["n"] for p in pts]; ys = [p["lat_us"] for p in pts]; ws = [p["n_exec"] for p in pts]
        fit, model, why = None, None, None
        if len(pts) < 3:
            why = f"only {len(pts)} distinct n"
        else:
            fit = wls(xs, ys, ws)
            if fit is None:
                why = "no spread in n"
            elif fit[1] < 0:
                why = f"negative slope ({fit[1]:.1f} us per unit)"
        resid = None
        if fit is not None and why is None:
            a0, b, r2 = fit
            ref = a0 + b * n_ref
            model = {"lat_ref_us": int(round(max(ref, 0))), "lat_slope_us": round(b, 3),
                     "en_ref_uwh": round(P * max(ref, 0) / 3600.0, 3), "en_slope_uwh": round(P * b / 3600.0, 5)}
            resid = [abs(y - (a0 + b * x)) / max(a0 + b * x, 1) for x, y in zip(xs, ys)]
        entry = {"module": module, "n_ref": n_ref, "power_w": round(P, 4), "points": pts, "model": model}
        entry["fit"] = ({"r2": round(fit[2], 4), "resid_p95_pct": round(100 * p95(resid), 2),
                         "resid_max_pct": round(100 * max(resid), 2), "n_points": len(pts)}
                        if model else {"refused": why, "n_points": len(pts)})
        if first[slot]:
            entry["first_batch_ratio_median"] = round(statistics.median(first[slot]), 3)
        out["slots"][str(slot)] = entry
        report.append((slot, module, len(pts), xs[0], xs[-1], entry["fit"], P))

    # Validation: measured energy above the floor vs sum of P x latency.
    val = []
    for r in runs:
        e, fb, fa = r.get("energy") or {}, r.get("floor_before_w"), r.get("floor_after_w")
        if not e or fb is None or fa is None:
            continue
        floor = (fb + fa) / 2
        meas_uwh = (e["energy_wh"] - floor * e["measured_s"] / 3600.0) * 1e6
        pred_uwh = sum(hint[x["param_id"] + 1]["power_w"] * x["latency_us"] / 3600.0 for x in r["execs"])
        batches = int(r["spec"]["n"])
        val.append({"label": r["label"], "images": r["spec"]["images"], "pipeline": r["spec"]["pipeline"],
                    "measured_uwh": round(meas_uwh, 1), "predicted_uwh": round(pred_uwh, 1),
                    "ratio": round(meas_uwh / pred_uwh, 4) if pred_uwh else None,
                    "excess_per_batch_uwh": round((meas_uwh - pred_uwh) / batches, 1)})
    out["_validation"] = val

    # U3, directly, per pipeline (the calibration's error is per operating
    # point, so pooling pipelines confounds it with input size). Within a
    # pipeline: measured = a * predicted + b * batches (a: the calibrated
    # power's scale for that pipeline; b: per-batch work outside module
    # latency -- ingest, file writes, process start-up). If a stage's mean
    # power were not flat in its input size, the residual would drift with
    # the run's mean downstream n: its slope per e-fold of n is the test.
    usable = [r for r in runs if r.get("energy") and r.get("floor_before_w") is not None
              and r.get("floor_after_w") is not None]
    for v, r in zip(val, usable):
        v["batches"] = int(r["spec"]["n"])
        ns = [x["n_input"] for x in r["execs"] if x["module"] not in FRAME_LEVEL]
        v["mean_n_downstream"] = statistics.mean(ns) if ns else 1.0

    def fit_ab(vs):
        X = [(v["predicted_uwh"], v["batches"]) for v in vs]
        y = [v["measured_uwh"] for v in vs]
        s11 = sum(a * a for a, _ in X); s12 = sum(a * b for a, b in X); s22 = sum(b * b for _, b in X)
        t1 = sum(a * yy for (a, _), yy in zip(X, y)); t2 = sum(b * yy for (_, b), yy in zip(X, y))
        det = s11 * s22 - s12 * s12
        if not det:
            return None
        A = (t1 * s22 - t2 * s12) / det
        B = (s11 * t2 - s12 * t1) / det
        rel = [(yy - (A * a + B * b)) / yy for (a, b), yy in zip(X, y)]
        ln = [math.log(v["mean_n_downstream"]) for v in vs]
        mx = statistics.mean(ln); my = statistics.mean(rel)
        sxx = sum((x - mx) ** 2 for x in ln)
        slope = sum((x - mx) * (r_ - my) for x, r_ in zip(ln, rel)) / sxx if sxx else 0.0
        for v, r_ in zip(vs, rel):
            v["resid_rel"] = round(r_, 4)
        return {"scale_a": round(A, 4), "per_batch_uwh_b": round(B, 1), "n_runs": len(vs),
                "resid_rel_p95_pct": round(100 * p95([abs(x) for x in rel]), 2),
                "resid_rel_slope_per_e_fold_n_pct": round(100 * slope, 2),
                "mean_n_range": [round(min(v["mean_n_downstream"] for v in vs), 1),
                                 round(max(v["mean_n_downstream"] for v in vs), 1)]}
    u3 = {}
    for pl in sorted({v["pipeline"] for v in val}):
        vs = [v for v in val if v["pipeline"] == pl]
        if len(vs) >= 4:
            u3[str(pl)] = fit_ab(vs)
    out["_u3"] = u3
    Path(a.out).write_text(json.dumps(out, indent=1))

    print(f"{len(runs)} runs used, {len(skipped)} skipped")
    for lab, why in skipped:
        print(f"  skipped {lab}: {why}")
    print(f"\n{'slot':>4} {'module':32} {'pts':>3} {'n range':>11} {'P (W)':>6}  fit")
    for slot, module, k, lo, hi, f, P in report:
        fs = (f"r2 {f['r2']:.3f}  resid p95 {f['resid_p95_pct']:.1f} %  max {f['resid_max_pct']:.1f} %"
              if "r2" in f else f"refused: {f['refused']}")
        print(f"{slot:>4} {module:32} {k:>3} {lo:>5}-{hi:<5} {P:>6.2f}  {fs}")
    if val:
        print(f"\nenergy validation ({len(val)} runs): measured/predicted ratio "
              f"median {statistics.median(v['ratio'] for v in val):.3f}, "
              f"range {min(v['ratio'] for v in val):.3f}-{max(v['ratio'] for v in val):.3f}")
    for pl, f in (u3 or {}).items():
        if f:
            print(f"U3 pipeline {pl} ({f['n_runs']} runs, mean n {f['mean_n_range'][0]}-{f['mean_n_range'][1]}): "
                  f"a = {f['scale_a']}, b = {f['per_batch_uwh_b']} uWh/batch; residual p95 "
                  f"{f['resid_rel_p95_pct']} %, drift {f['resid_rel_slope_per_e_fold_n_pct']} % per e-fold of n")
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
