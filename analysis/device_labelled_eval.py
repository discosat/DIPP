#!/usr/bin/env python3
"""Score a classifier on hand-labelled tiles from real device imagery.

    analysis/device_labelled_eval.py [--model ...] [--models-dir ...]

EuroSAT accuracy says whether a model works on EuroSAT. This says whether it
works on what the satellite actually produces, which is the only number that
justifies anything the paper claims. Labels are in
`stage4.5-models/device_labels.json`: only unambiguous tiles -- open water with
no coastline, dense urban with no fields -- so a model that fails here is not
failing on a hard case.
"""
import argparse, json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from preprocess_replica import device_frame, tiles, CLASSES

DATA = os.path.expanduser("~/dipp-datasets/cs12-frames")   # CloudSEN12+ frames; the koge/amager frames are RETIRED


CALIB = json.load(open(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "stage4.5-models", "calibration.json")))


def calibrated_tiles(path):
    """Tiles as the fixed-calibration pipeline now produces them.

    device_frame() reproduces the legacy cv::normalize path, so re-derive from
    the raw 12-bit demosaic and apply the same affine demosaic_module.cpp does.
    """
    import cv2
    from preprocess_replica import H, W, TILE
    raw = np.fromfile(path, dtype="<u2").reshape(H, W)
    dem = cv2.rotate(cv2.cvtColor(raw, cv2.COLOR_BayerRG2BGR), cv2.ROTATE_180)
    f = np.clip(dem.astype(np.float32) * np.array(CALIB["gain"], np.float32)
                + np.array(CALIB["offset"], np.float32), 0, 255).astype(np.uint8)
    return [f[y:y + TILE, x:x + TILE]
            for y in range(0, H - TILE + 1, TILE)
            for x in range(0, W - TILE + 1, TILE)]


def evaluate(model_path, labels, data_dir, prep=None, calibrate=True):
    from ai_edge_litert.interpreter import Interpreter
    it = Interpreter(model_path=model_path); it.allocate_tensors()
    di, do = it.get_input_details()[0], it.get_output_details()[0]
    size = int(di["shape"][1])
    per_class, preds = {}, {}
    for scene, byclass in labels.items():
        if scene.startswith("_"):   # documentation keys, not scenes
            continue
        src = os.path.join(data_dir, f"{scene}.bayerRG")
        ts = calibrated_tiles(src) if calibrate else list(tiles(device_frame(src)))
        for cname, idxs in byclass.items():
            want = CLASSES.index(cname)
            for i in idxs:
                a = ts[i]
                if prep is not None:
                    a = prep(a)
                if a.shape[0] != size:
                    import cv2
                    a = cv2.resize(a, (size, size), interpolation=cv2.INTER_AREA)
                it.set_tensor(di["index"], np.ascontiguousarray(a)[None, ...])
                it.invoke()
                p = int(np.argmax(it.get_tensor(do["index"])[0]))
                per_class.setdefault(cname, []).append(p == want)
                preds.setdefault(cname, []).append(p)
    return per_class, preds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default="../tf-dipp-module")
    ap.add_argument("--models", nargs="*",
                    default=["custom_conv_model_landcover", "mobilenetv2_05_landcover",
                             "mobilenetv2_14_landcover"])
    ap.add_argument("--data", default=DATA)
    ap.add_argument("--raw", action="store_true",
                    help="legacy cv::normalize path instead of the calibration")
    a = ap.parse_args()
    spec = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "stage4.5-models", "device_labels.json")))
    labels = spec["labels"]
    n = sum(len(v) for s in labels.values() for v in s.values())
    print(f"{n} hand-labelled tiles from {len(labels)} real scenes "
          f"({'raw/legacy' if a.raw else 'calibrated'} radiometry)\n")
    print(f"{'model':32} {'overall':>8} {'SeaLake':>9} {'Residential':>12}   most-predicted")
    print("-" * 92)
    for m in a.models:
        p = os.path.join(a.models_dir, f"{m}.tflite")
        if not os.path.exists(p):
            continue
        pc, pr = evaluate(p, labels, a.data, calibrate=not a.raw)
        allv = [x for v in pc.values() for x in v]
        import collections
        top = collections.Counter(x for v in pr.values() for x in v).most_common(2)
        top = " ".join(f"{CLASSES[c]}={k}" for c, k in top)
        print(f"{m:32} {np.mean(allv):7.1%} {np.mean(pc['SeaLake']):8.1%} "
              f"{np.mean(pc['Residential']):11.1%}   {top}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
