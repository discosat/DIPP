#!/usr/bin/env python3
"""Validate the classifier models in isolation, against the data they were trained for.

    analysis/model_isolation.py --data ~/dipp1-experiments/tensorflow-conversion/EuroSat-working/testing

Answers one question before any on-device debugging: is this a *model* problem
or a *pipeline* problem? The models are scored on the EuroSAT `testing/` split
using exactly the preprocessing training used -- RGB, bilinear resize to
224x224, raw 0-255 (the `Rescaling(1./255)` layer is commented out in the
training script, and the input quantisation scale is ~1.0, which agrees).

This split is a genuine holdout: the training script builds `val_ds` from
TRAIN_DIR, not TEST_DIR, so no held-out number has ever been computed for these
models.

`--channel-order bgr` reproduces what the device actually feeds them
(`demosaic_module.cpp` emits BGR and nothing swaps it back), so the accuracy
cost of that one defect can be read straight off the difference.
"""

import argparse
import collections
import json
import os
import sys
import time

import numpy as np
from PIL import Image
from ai_edge_litert.interpreter import Interpreter

DEFAULT_DATA = os.path.expanduser(
    "~/dipp1-experiments/tensorflow-conversion/EuroSat-working/testing")
MODELS = ["custom_conv_model_landcover", "mobilenetv2_05_landcover",
          "mobilenetv2_14_landcover"]


def load_image(path, size, channel_order):
    """Match tf.keras.utils.image_dataset_from_directory(image_size=(224,224))."""
    im = Image.open(path).convert("RGB").resize((size, size), Image.BILINEAR)
    a = np.asarray(im, dtype=np.uint8)
    if channel_order == "bgr":
        a = a[:, :, ::-1]
    return a


def evaluate(model_path, classes, files, channel_order, limit=None):
    interp = Interpreter(model_path=model_path)
    interp.allocate_tensors()
    inp = interp.get_input_details()[0]
    out = interp.get_output_details()[0]
    size = int(inp["shape"][1])
    idx_in, idx_out = inp["index"], out["index"]

    n_classes = len(classes)
    conf = np.zeros((n_classes, n_classes), dtype=np.int64)
    t0 = time.time()
    total = 0
    for label, path in files if limit is None else files[:limit]:
        a = load_image(path, size, channel_order)
        interp.set_tensor(idx_in, a[None, ...])
        interp.invoke()
        pred = int(np.argmax(interp.get_tensor(idx_out)[0]))
        conf[label, pred] += 1
        total += 1
    return conf, total, time.time() - t0


def report(name, classes, conf, total, secs, channel_order):
    correct = int(np.trace(conf))
    acc = correct / total if total else 0.0
    per_class = conf.sum(axis=1)
    recall = np.divide(np.diag(conf), per_class,
                       out=np.zeros(len(classes)), where=per_class > 0)
    # A model that has collapsed onto one class scores ~1/n but looks "alive"
    # in a spot check, so report the prediction spread explicitly.
    pred_counts = conf.sum(axis=0)
    n_predicted = int((pred_counts > 0).sum())
    print(f"\n=== {name}  [{channel_order.upper()}] ===")
    print(f"  top-1 accuracy : {acc:6.2%}  ({correct}/{total})   "
          f"chance = {1/len(classes):.1%}")
    print(f"  classes ever predicted: {n_predicted}/{len(classes)}"
          + ("   <-- COLLAPSED" if n_predicted <= 2 else ""))
    print(f"  {secs:.1f}s  ({1000*secs/max(total,1):.1f} ms/image)")
    print("  per-class recall:")
    for i, c in enumerate(classes):
        bar = "#" * int(round(recall[i] * 40))
        print(f"    {i} {c:<22} {recall[i]:6.2%}  n={per_class[i]:<5} {bar}")
    return {"model": name, "channel_order": channel_order, "accuracy": acc,
            "correct": correct, "total": total,
            "classes_predicted": n_predicted,
            "recall": {c: float(recall[i]) for i, c in enumerate(classes)},
            "confusion": conf.tolist()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--models-dir", default="../tf-dipp-module")
    ap.add_argument("--models", nargs="*", default=MODELS)
    ap.add_argument("--channel-order", nargs="*", default=["rgb", "bgr"],
                    choices=["rgb", "bgr"])
    ap.add_argument("--limit", type=int, help="images per run (debug)")
    ap.add_argument("-o", "--out", default="analysis/stage4.5-models")
    a = ap.parse_args()

    if not os.path.isdir(a.data):
        sys.exit(f"no EuroSAT testing split at {a.data}")
    classes = sorted(d for d in os.listdir(a.data)
                     if os.path.isdir(os.path.join(a.data, d)))
    files = []
    for i, c in enumerate(classes):
        d = os.path.join(a.data, c)
        for f in sorted(os.listdir(d)):
            if f.lower().endswith((".jpg", ".jpeg", ".png")):
                files.append((i, os.path.join(d, f)))
    print(f"{len(files)} images, {len(classes)} classes: {classes}")

    results = []
    for m in a.models:
        p = os.path.join(a.models_dir, f"{m}.tflite")
        if not os.path.exists(p):
            print(f"  skip {m}: not found at {p}", file=sys.stderr)
            continue
        for order in a.channel_order:
            conf, total, secs = evaluate(p, classes, files, order, a.limit)
            results.append(report(m, classes, conf, total, secs, order))

    os.makedirs(a.out, exist_ok=True)
    dest = os.path.join(a.out, "isolation.json")
    json.dump({"classes": classes, "n_images": len(files), "results": results},
              open(dest, "w"), indent=2)
    print(f"\nwrote {dest}")

    best = max((r["accuracy"] for r in results), default=0.0)
    print(f"\nBest accuracy across all runs: {best:.2%}")
    if best < 0.30:
        print("GATE FAILED: near-chance on the data these models were trained "
              "for. The exported artifacts are the problem, not the pipeline.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
