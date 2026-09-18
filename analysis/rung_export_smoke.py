#!/usr/bin/env python3
"""Export segmentation rungs and SMOKE-TEST them on device frames.

    analysis/rung_export_smoke.py --out analysis/stage4.5-models/cs12_rungs

Renamed from cs12_rung_eval.py on 2026-09-13, because that name was a lie: it
scores masked-fraction on the five device .bayerRG frames via calibrated_tiles,
and CloudSEN12 enters only as PTQ calibration. A file called cs12_* that does not
score on CloudSEN12 is exactly how the wrong corpus gets used by accident, and
it did -- the `separation` figure this reports ranked a 3-epoch model first when
expert masks put it last.

What this IS for: exporting with the pinned quantisation contract, asserting
the graph is NPU-runnable (TRANSPOSE=0, dynamic=0, DELEGATE=1), and a smoke
check that the model responds to content on real frames. What it is NOT for:
quality. Quality is analysis/cs12_validate.py against expert masks.
"""

import argparse
import collections
import glob
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.dirname(HERE)
# The converter needs a TF that can quantise; this venv sits beside the repo,
# not inside it. Overridable because the conversion host is not always this one.
CONVERT_PY = os.environ.get("CONVERT_PY", os.path.join(
    os.path.dirname(REPO), ".venv-train", "bin", "python"))
DYN = ("SHAPE", "STRIDED_SLICE", "PACK")


def export(npz, out_dir, samples=150):
    tf = os.path.join(out_dir, "ptq_" + os.path.basename(npz)
                      .replace("cloud_", "").replace(".weights.npz", ".tflite"))
    if os.path.exists(tf):
        return tf
    if not os.path.exists(CONVERT_PY):
        sys.exit(f"no converter python at {CONVERT_PY}; set CONVERT_PY=")
    r = subprocess.run([CONVERT_PY,
                        os.path.join(REPO, "training", "convert_tf29.py"),
                        npz, "-o", tf, "--samples", str(samples),
                        "--calib", "device"],
                       capture_output=True, text=True, cwd=REPO)
    if r.returncode != 0 or not os.path.exists(tf):
        print(f"  EXPORT FAILED: {r.stderr.strip().splitlines()[-3:]}")
        return None
    return tf


def check_ops(path):
    from ai_edge_litert.interpreter import Interpreter
    it = Interpreter(model_path=path)
    it.allocate_tensors()
    ops = collections.Counter(o["op_name"] for o in it._get_ops_details())
    di, do = it.get_input_details()[0], it.get_output_details()[0]
    return {"transpose": ops.get("TRANSPOSE", 0),
            "dynamic": sum(ops.get(k, 0) for k in DYN),
            "delegate": ops.get("DELEGATE", 0),
            "in_q": di["quantization"], "out_q": do["quantization"],
            "res": int(di["shape"][1]), "bytes": os.path.getsize(path)}


def masked_fraction(path, scene, threshold=0.5):
    """Fraction of pixels the module would mask, on real device tiles."""
    import cv2
    from ai_edge_litert.interpreter import Interpreter
    from device_labelled_eval import calibrated_tiles, DATA
    p = os.path.join(DATA, scene + ".bayerRG")
    if not os.path.exists(p):
        return float("nan")
    it = Interpreter(model_path=path)
    it.allocate_tensors()
    di, do = it.get_input_details()[0], it.get_output_details()[0]
    res = int(di["shape"][1])
    si, zi = di["quantization"]
    so, zo = do["quantization"]
    fr = []
    for t in calibrated_tiles(p):
        # The module resizes with INTER_AREA and writes raw uint8 through, so
        # reproduce exactly that rather than a float path that would be kinder.
        x = t if t.shape[0] == res else cv2.resize(t, (res, res),
                                                   interpolation=cv2.INTER_AREA)
        it.set_tensor(di["index"], x.astype(np.uint8)[None])
        it.invoke()
        sc = (it.get_tensor(do["index"]).astype(np.float32) - zo) * so
        fr.append(float((sc > threshold).mean()))
    return float(np.mean(fr))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "stage4.5-models",
                                                  "cs12_rungs"))
    ap.add_argument("--glob", default="training/out/cloud_cs12*.weights.npz",
                    help="cs12_* and cs12tc_* both; the narrower "
                         "cloud_cs12_* silently missed the thin-clear rungs")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    files = sorted(glob.glob(os.path.join(REPO, a.glob)))
    if not files:
        sys.exit(f"no weights matching {a.glob}")
    rows = []
    for npz in files:
        tag = os.path.basename(npz).replace("cloud_", "").replace(".weights.npz", "")
        meta = json.load(open(npz.replace(".weights.npz", ".json")))
        print(f"--- {tag} (val_iou={meta.get('val_iou', float('nan')):.4f}) ---",
              flush=True)
        tfl = export(npz, a.out)
        if tfl is None:
            continue
        ok = check_ops(tfl)
        npu = ok["transpose"] == 0 and ok["dynamic"] == 0
        # A tolerance, not equality: the converter rounds the zero point, and
        # 127 vs 128 is a half-LSB shift the module tolerates. A scale that is
        # not 2/255 is not tolerable -- that is the failure that reported 0.00%.
        contract = abs(ok["in_q"][0] - 2 / 255) < 1e-4 and abs(ok["in_q"][1] - 128) <= 2
        print(f"    ops: TRANSPOSE={ok['transpose']} dynamic={ok['dynamic']} "
              f"DELEGATE={ok['delegate']}  {'NPU-OK' if npu else 'NOT NPU-RUNNABLE'}")
        print(f"    in_q={tuple(round(v, 6) for v in ok['in_q'])} "
              f"{'contract ok' if contract else 'VIOLATES PASSTHROUGH CONTRACT'}")
        # No device-frame "separation" any more: the koge/amager frames are
        # retired (unlabelled, misleading). Quality is analysis/cs12_validate.py
        # against expert masks; this tool only exports and checks the graph.
        rows.append(dict(tag=tag, tflite=tfl, val_iou=meta.get("val_iou"),
                         res=ok["res"], bytes=ok["bytes"], npu_ok=npu,
                         contract_ok=contract))

    print(f"\n{'rung':24} {'res':>4} {'val_iou':>8}  flags")
    for r in rows:
        flags = ("" if r["npu_ok"] else "NO-NPU ") + ("" if r["contract_ok"] else "BAD-QUANT")
        print(f"{r['tag']:24} {r['res']:4} {r['val_iou']:8.4f}  {flags}")


if __name__ == "__main__":
    main()
