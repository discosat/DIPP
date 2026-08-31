#!/usr/bin/env python3
"""Quantise a trained Keras model to the uint8 TFLite the module expects.

    training/export_tflite.py training/out/landcover_low.keras

The module reads the interpreter's own input shape and resizes the tile into
it, so the only hard contract is uint8 in / uint8 out. Full int8 quantisation
with uint8 I/O is what the existing models use and what the VX delegate on the
board accelerates.

The representative dataset is drawn through the *same* photometric augmentation
used in training. That matters: quantisation ranges calibrated on pristine
EuroSAT would clip exactly the shifted radiometry the augmentation exists to
cover, undoing the fix at export time.
"""

import argparse
import json
import os
import sys

import numpy as np
import tensorflow as tf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from train_landcover import DATA, photometric, SEED   # noqa: E402


def representative(res, n=300, batch=16):
    ds = tf.keras.utils.image_dataset_from_directory(
        os.path.join(DATA, "training"), image_size=(res, res),
        batch_size=batch, seed=SEED, shuffle=True)
    seen = 0
    for x, _ in ds:
        x = photometric(x)
        for i in range(x.shape[0]):
            yield [tf.cast(x[i:i + 1], tf.float32)]
            seen += 1
            if seen >= n:
                return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keras_model")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()

    meta_path = a.keras_model.replace(".keras", ".json")
    res = json.load(open(meta_path))["res"] if os.path.exists(meta_path) else None

    model = tf.keras.models.load_model(a.keras_model)
    if res is None:
        res = int(model.input_shape[1])

    conv = tf.lite.TFLiteConverter.from_keras_model(model)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.representative_dataset = lambda: representative(res)
    conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = tf.uint8
    conv.inference_output_type = tf.uint8
    blob = conv.convert()

    out = a.out or a.keras_model.replace(".keras", ".tflite")
    open(out, "wb").write(blob)

    it = tf.lite.Interpreter(model_content=blob)
    it.allocate_tensors()
    di, do = it.get_input_details()[0], it.get_output_details()[0]
    print(f"wrote {out}  ({len(blob):,} B)")
    print(f"  in  {di['shape']} {di['dtype'].__name__} q={di['quantization']}")
    print(f"  out {do['shape']} {do['dtype'].__name__} q={do['quantization']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
