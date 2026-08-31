#!/usr/bin/env python3
"""Validate the cloud-segmentation models in isolation, on 38-Cloud.

    analysis/segmentation_isolation.py --limit 600

The classification side has an equivalent (`model_isolation.py`). Same purpose:
establish whether effort buys quality *before* anything is retrained, because
an effort ladder that does not improve with effort makes the whole adaptive
argument pointless.

The export's intended input scaling is ambiguous -- the conversion script
divides by 65536 to get floats in [0,1], but the exported input tensor is uint8
with scale ~1.0, i.e. a dequantised range of [0, 256]. Rather than guess, every
plausible 16-bit -> 8-bit mapping is tried and the best reported; if none of
them works the model is broken regardless of which was intended.

Polarity is also measured rather than assumed: the module keeps pixels whose
score is < 0.5 (class_index 0), so which side of the threshold means "cloud"
has to be established from data.
"""

import argparse
import glob
import os
import sys

import numpy as np
from PIL import Image
from ai_edge_litert.interpreter import Interpreter

ROOT = os.path.expanduser("~/dipp1-experiments/tensorflow-conversion/38cloud/38-Cloud_training")
MODELS = ["custom_tiny_conv_unet", "mobilenetv2_14_unet"]


def load_patch(name, size):
    ch = []
    for band in ("red", "green", "blue"):
        p = os.path.join(ROOT, f"train_{band}", f"{band}_{name}.TIF")
        ch.append(np.array(Image.open(p).resize((size, size), Image.BILINEAR),
                           dtype=np.float32))
    img = np.stack(ch, axis=-1)
    gt = np.array(Image.open(os.path.join(ROOT, "train_gt", f"gt_{name}.TIF"))
                  .resize((size, size), Image.NEAREST))
    return img, (gt > 127)


def to_uint8(img, mapping):
    if mapping == "div65536":       # what the conversion script's generator did
        return np.clip(img / 65536.0 * 255.0, 0, 255).astype(np.uint8)
    if mapping == "shift8":         # high byte
        return np.clip(img / 256.0, 0, 255).astype(np.uint8)
    if mapping == "minmax":         # what the device does to its own frames
        lo, hi = img.min(), img.max()
        return np.clip((img - lo) * 255.0 / max(hi - lo, 1), 0, 255).astype(np.uint8)
    if mapping == "pct2":           # robust stretch
        lo, hi = np.percentile(img, 2), np.percentile(img, 98)
        return np.clip((img - lo) * 255.0 / max(hi - lo, 1), 0, 255).astype(np.uint8)
    raise ValueError(mapping)


def iou_f1(pred, gt):
    inter = np.logical_and(pred, gt).sum()
    union = np.logical_or(pred, gt).sum()
    tp, fp, fn = inter, np.logical_and(pred, ~gt).sum(), np.logical_and(~pred, gt).sum()
    iou = inter / union if union else 1.0
    f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 1.0
    return iou, f1


def evaluate(model_path, names, mapping):
    it = Interpreter(model_path=model_path)
    it.allocate_tensors()
    di, do = it.get_input_details()[0], it.get_output_details()[0]
    size = int(di["shape"][1])
    scale, zp = do["quantization"]
    ious, f1s, ious_inv, cloud_frac, gt_frac = [], [], [], [], []
    for n in names:
        img, gt = load_patch(n, size)
        it.set_tensor(di["index"], to_uint8(img, mapping)[None, ...])
        it.invoke()
        raw = it.get_tensor(do["index"])[0].astype(np.float32).reshape(size, size)
        score = (raw - zp) * scale
        pred = score >= 0.5
        i, f = iou_f1(pred, gt)
        ii, _ = iou_f1(~pred, gt)
        ious.append(i); f1s.append(f); ious_inv.append(ii)
        cloud_frac.append(pred.mean()); gt_frac.append(gt.mean())
    return (float(np.mean(ious)), float(np.mean(f1s)), float(np.mean(ious_inv)),
            float(np.mean(cloud_frac)), float(np.mean(gt_frac)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default="../tf-dipp-module")
    ap.add_argument("--models", nargs="*", default=MODELS)
    ap.add_argument("--limit", type=int, default=400)
    a = ap.parse_args()

    csv = os.path.join(os.path.dirname(ROOT), "training_patches_38-cloud_nonempty.csv")
    names = [l.strip() for l in open(csv)][1:]
    rng = np.random.default_rng(43)
    names = list(rng.permutation(names)[:a.limit])
    print(f"{len(names)} patches from 38-Cloud (seeded sample)\n")

    print(f"{'model':24} {'mapping':10} {'IoU':>7} {'F1':>7} {'IoU_inv':>8} "
          f"{'pred_cloud':>11} {'gt_cloud':>9}")
    print("-" * 82)
    best = {}
    for m in a.models:
        p = os.path.join(a.models_dir, f"{m}.tflite")
        if not os.path.exists(p):
            print(f"  skip {m}", file=sys.stderr); continue
        for mapping in ("div65536", "shift8", "minmax", "pct2"):
            iou, f1, inv, cf, gf = evaluate(p, names, mapping)
            print(f"{m:24} {mapping:10} {iou:7.3f} {f1:7.3f} {inv:8.3f} "
                  f"{cf:11.1%} {gf:9.1%}")
            if iou > best.get(m, (0,))[0]:
                best[m] = (iou, mapping)
        print()
    print("best IoU per model:")
    for m, (iou, mapping) in best.items():
        print(f"  {m:24} {iou:.3f}  ({mapping})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
