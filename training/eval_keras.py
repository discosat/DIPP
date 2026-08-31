#!/usr/bin/env python3
"""Top-1 of a float Keras model on the EuroSAT *testing* split.

    training/eval_keras.py training/out/landcover_high.keras

Exists to separate two very different failures that look identical from the
tflite side: a model that never learned, and a model that learned and was then
destroyed by post-training quantisation. The rung's validation accuracy cannot
tell them apart because it is measured before export.
"""
import argparse, json, os, sys
import numpy as np, tensorflow as tf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from train_landcover import DATA, SEED  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keras_model")
    ap.add_argument("--limit", type=int, default=1500)
    a = ap.parse_args()

    res = int(json.load(open(a.keras_model.replace(".keras", ".json")))["res"])
    test = os.path.join(DATA, "testing")
    classes = sorted(d for d in os.listdir(test)
                     if os.path.isdir(os.path.join(test, d)))
    files = [(i, os.path.join(test, c, f))
             for i, c in enumerate(classes)
             for f in sorted(os.listdir(os.path.join(test, c)))
             if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    rng = np.random.default_rng(SEED)
    if a.limit and a.limit < len(files):
        files = [files[i] for i in rng.permutation(len(files))[:a.limit]]

    m = tf.keras.models.load_model(a.keras_model)
    ok = 0
    for i in range(0, len(files), 64):
        chunk = files[i:i + 64]
        x = np.stack([np.array(tf.image.resize(
            tf.io.decode_image(tf.io.read_file(p), channels=3,
                               expand_animations=False), (res, res)))
            for _, p in chunk])
        pred = np.argmax(m.predict(x, verbose=0), axis=1)
        ok += int((pred == np.array([c for c, _ in chunk])).sum())
    print(f"FLOAT_TOP1 {os.path.basename(a.keras_model)} "
          f"{ok / len(files):.4f}  ({ok}/{len(files)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
