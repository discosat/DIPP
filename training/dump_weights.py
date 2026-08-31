#!/usr/bin/env python3
"""Dump a Keras model's weights as a plain .npz, in layer order.

    training/dump_weights.py training/out/landcover_sm_cnn064.keras

The board runs TFLite 2.9.1 and a model converted by TF 2.17 does not execute
correctly on it -- it returns one class for every input, silently. So training
happens under the modern stack and conversion has to happen under 2.9, and the
two cannot exchange a `.keras` file: Keras 3's format is unreadable by Keras
2.9. Raw arrays are the one thing both agree on, provided the architecture is
rebuilt identically on the other side, which `convert_tf29.py` does from the
same builder function.
"""
import argparse, os, sys
import numpy as np
import tensorflow as tf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keras_model")
    a = ap.parse_args()
    # compile=False: the cloud models carry a custom iou_metric that cannot be
    # deserialised without registering it, and weights do not need the
    # optimiser or metrics anyway.
    m = tf.keras.models.load_model(a.keras_model, compile=False)
    w = m.get_weights()
    out = a.keras_model.replace(".keras", ".weights.npz")
    np.savez(out, *w)
    print(f"wrote {out}  ({len(w)} arrays, "
          f"{sum(x.size for x in w):,} params)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
