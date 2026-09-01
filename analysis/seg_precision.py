#!/usr/bin/env python3
"""Precision/recall of the cloud models, and what the decision threshold buys.

    analysis/seg_precision.py

The two error directions are not equally costly here. A false negative leaves
cloud in the downlinked tile: wasteful, but the pixels survive and the ground
can still see them. A false positive blacks out ground that was never cloudy,
and that data is gone -- it is never downlinked and cannot be recovered.

IoU = TP/(TP+FP+FN) weighs those two identically, so it cannot express the
asymmetry. This reports precision (1 - the irrecoverable-loss rate) separately,
and sweeps the per-pixel decision threshold, which the module currently hardcodes
at 0.5 (tflite_binary_segmentation.cpp).
"""

import argparse
import glob
import os
import sys

import numpy as np
from PIL import Image
from ai_edge_litert.interpreter import Interpreter

ROOT = os.path.expanduser(
    "~/dipp1-experiments/tensorflow-conversion/38cloud/38-Cloud_training")


def load(name, size):
    ch = [np.array(Image.open(os.path.join(ROOT, f"train_{b}", f"{b}_{name}.TIF"))
                   .resize((size, size), Image.BILINEAR), dtype=np.float32)
          for b in ("red", "green", "blue")]
    img = np.stack(ch, -1)
    gt = np.array(Image.open(os.path.join(ROOT, "train_gt", f"gt_{name}.TIF"))
                  .resize((size, size), Image.NEAREST)) > 127
    lo, hi = np.percentile(img, 2), np.percentile(img, 98)
    u8 = np.clip((img - lo) * 255.0 / max(hi - lo, 1), 0, 255).astype(np.uint8)
    return u8, gt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=["tf29_cloud_cl_low",
                                                    "tf29_cloud_cl_high"])
    ap.add_argument("--models-dir", default="training/out")
    ap.add_argument("--limit", type=int, default=150)
    a = ap.parse_args()

    csv = os.path.join(os.path.dirname(ROOT), "training_patches_38-cloud_nonempty.csv")
    names = [l.strip() for l in open(csv)][1:]
    rng = np.random.default_rng(43)
    names = list(rng.permutation(names)[:a.limit])
    print(f"{len(names)} patches from 38-Cloud\n")

    for m in a.models:
        p = os.path.join(a.models_dir, f"{m}.tflite")
        if not os.path.exists(p):
            print(f"  skip {m}", file=sys.stderr); continue
        it = Interpreter(model_path=p); it.allocate_tensors()
        di, do = it.get_input_details()[0], it.get_output_details()[0]
        size = int(di["shape"][1]); scale, zp = do["quantization"]
        scores, gts = [], []
        for n in names:
            x, gt = load(n, size)
            it.set_tensor(di["index"], x[None, ...]); it.invoke()
            raw = it.get_tensor(do["index"])[0].astype(np.float32).reshape(size, size)
            scores.append((raw - zp) * scale); gts.append(gt)
        s = np.concatenate([v.ravel() for v in scores])
        g = np.concatenate([v.ravel() for v in gts])
        print(f"=== {m} ===")
        print(f"{'thresh':>7}{'IoU':>8}{'precision':>11}{'recall':>9}"
              f"{'FP rate':>9}{'ground lost':>13}")
        for t in (0.3, 0.5, 0.6, 0.7, 0.8, 0.9):
            pred = s >= t
            tp = np.logical_and(pred, g).sum()
            fp = np.logical_and(pred, ~g).sum()
            fn = np.logical_and(~pred, g).sum()
            iou = tp / max(tp + fp + fn, 1)
            prec = tp / max(tp + fp, 1)
            rec = tp / max(tp + fn, 1)
            # Of all genuinely clear ground pixels, how many get blacked out.
            lost = fp / max((~g).sum(), 1)
            print(f"{t:7.1f}{iou:8.3f}{prec:11.3f}{rec:9.3f}"
                  f"{fp / len(g):9.3f}{lost:12.1%}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
