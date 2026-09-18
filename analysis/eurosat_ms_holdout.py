#!/usr/bin/env python3
"""Top-1 of an exported classifier on the EuroSAT-MS `testing` holdout.

    analysis/eurosat_ms_holdout.py training/out/landcover_ms_low.tflite [...]

This is the reported quality figure for a classification rung. The images are
the 5,400 held-out EuroSAT-MS scenes rendered exactly as the board's demosaic
renders them (clip(reflectance x gain, 0, 255), uniform gain, zero offset) and
fed to the flatbuffer as RAW uint8, the way tflite_classification.cpp feeds it.
The gain comes from the model's own .json, so a model is always scored in the
domain it was trained for; pass --gain to override deliberately.

Two checks make the number trustworthy rather than merely produced:
  * the input quantisation must be (2/255, 128) -- anything else means the
    raw-uint8 passthrough feeds the wrong domain, and the accuracy collapses
    silently (the failure that cost a board round-trip on segmentation);
  * with --float, the same images go through the float graph rebuilt from the
    .weights.npz, so the quantisation loss is visible as a separate line.
"""

import argparse
import collections
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "training"))
NPZ = os.environ.get("EUROSAT_MS", os.path.expanduser(
    "~/dipp-datasets/eurosat-ms/eurosat_ms.npz"))


def render(X, gain, res):
    import cv2
    u8 = np.clip(X.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    if res == u8.shape[1]:
        return u8
    return np.stack([cv2.resize(t, (res, res), interpolation=cv2.INTER_LINEAR)
                     for t in u8])


def score(pred, y, classes):
    top1 = float((pred == y).mean())
    rec = {c: float((pred[y == i] == i).mean()) for i, c in enumerate(classes)}
    return top1, rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tflite", nargs="+")
    ap.add_argument("--gain", type=float, help="override the model json's gain")
    ap.add_argument("--limit", type=int, help="subset of the holdout (smoke)")
    ap.add_argument("--float", action="store_true",
                    help="also score the float graph from the .weights.npz")
    ap.add_argument("--json", help="append results here")
    a = ap.parse_args()
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:            # the training venv has full TF instead
        import tensorflow as tf
        Interpreter = tf.lite.Interpreter

    z = np.load(NPZ)
    te = np.flatnonzero(z["is_test"])
    if a.limit:
        te = np.random.default_rng(43).permutation(te)[:a.limit]
    X, y, classes = z["X"][te], z["y"][te].astype(np.int64), list(z["classes"])
    results = []
    for path in a.tflite:
        meta_path = path.replace(".tflite", ".json")
        meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
        gain = a.gain or meta.get("gain")
        if gain is None:
            sys.exit(f"{path}: no gain in {meta_path}; pass --gain")
        it = Interpreter(model_path=path); it.allocate_tensors()
        di, do = it.get_input_details()[0], it.get_output_details()[0]
        res = int(di["shape"][1]); si, zi = di["quantization"]
        # zp lands on 127 or 128 (a rounding tie at 127.5); one LSB is harmless.
        ok = abs(si - 2 / 255) < 1e-6 and zi in (127, 128)
        print(f"{os.path.basename(path)}: in {tuple(di['shape'])} "
              f"q=({si:.6f},{zi}) {'contract ok' if ok else 'CONTRACT VIOLATED'}")
        if not ok:
            sys.exit("input quantisation is not (2/255, 128); refusing to score")
        Xi = render(X, gain, res)
        pred = np.empty(len(y), np.int64)
        for i in range(len(y)):
            it.set_tensor(di["index"], Xi[i][None]); it.invoke()
            pred[i] = int(np.argmax(it.get_tensor(do["index"])[0]))
        top1, rec = score(pred, y, classes)
        row = dict(model=os.path.basename(path), gain=gain, res=res, n=len(y),
                   top1=top1, recall=rec)
        print(f"  tflite top-1 {100*top1:.2f}%  n={len(y)}  gain={gain}")
        if a.float and meta:
            import tensorflow as tf
            import train_landcover_ms as tm
            if meta["arch"] == "custom_conv":
                m = tm.build_custom_conv(res, len(classes))
            else:
                m = tm.build_mobilenet(res, float(meta["alpha"]), len(classes), None)
            if meta.get("qat"):
                import tensorflow_model_optimization as tfmot
                m = tfmot.quantization.keras.quantize_model(m)
            w = np.load(path.replace(".tflite", ".weights.npz"))
            m.set_weights([w[f"arr_{i}"] for i in range(len(w.files))])
            p = m.predict(Xi.astype(np.float32) / 127.5 - 1.0, batch_size=128,
                          verbose=0).argmax(1)
            ft, _ = score(p, y, classes)
            row["float_top1"] = ft
            print(f"  float  top-1 {100*ft:.2f}%   quantisation cost "
                  f"{100*(ft-top1):+.2f} pp")
        print("  recall " + "  ".join(f"{c[:6]}={100*r:.0f}" for c, r in rec.items()))
        results.append(row)
    if a.json:
        prev = json.load(open(a.json)) if os.path.exists(a.json) else []
        json.dump(prev + results, open(a.json, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
