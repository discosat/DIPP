#!/usr/bin/env python3
"""Train the cloud-segmentation effort ladder on 38-Cloud.

    training/train_cloud.py --effort low|high [--epochs N]

Same defect as the classification family, measured the same way: the shipped
`custom_tiny_conv_unet` (low, 75 KB, 120 ms, 220 uWh) scores IoU 0.594 and
`mobilenetv2_14_unet` (high, 5.4 MB, 1472 ms, 3540 uWh) scores 0.577 -- 12.3x
the latency and 16x the energy for slightly worse quality. Both barely clear
the constant-prediction floor, which is IoU 0.486 because the ground truth is
48.6% cloud.

Three things this does differently from whatever produced those:

  * **One radiometric domain.** 38-Cloud is 16-bit Landsat-8; the device now
    emits 8-bit tiles calibrated to the EuroSAT domain (see
    demosaic_module.cpp). Training data is mapped into that same domain with a
    per-band affine fitted on pooled statistics, so the segmenter, the
    classifier and the device all speak the same units. Without this the model
    learns a scale nothing else produces.
  * **RGB only.** 38-Cloud ships NIR, which makes cloud detection much easier
    and is exactly why it is tempting. The camera is an RGB Bayer sensor, so
    NIR is not available in flight and using it would inflate the result.
  * **Validation split from training.** The published conversion script built
    its validation generator from the training list, so no held-out number
    existed. `38-Cloud_test` is left untouched.

Effort scales by input resolution: the tiler keeps emitting 224x224 and the
module downsamples into whatever the model declares, so the cheap rung sees the
same ground at lower resolution rather than seeing less ground.
"""

import argparse
import glob
import json
import os
import sys

import numpy as np
import tensorflow as tf

ROOT = os.environ.get("CLOUD38_DIR", os.path.expanduser(
    "~/dipp1-experiments/tensorflow-conversion/38cloud/38-Cloud_training"))
SEED = 43
# Target domain: EuroSAT pooled per-band statistics, the same target the
# demosaic calibration maps the device into.
EU_MU = np.array([87.5, 97.0, 104.0], np.float32)
EU_SD = np.array([50.9, 34.5, 29.3], np.float32)

LADDER = {
    "low":  dict(res=128, width=8,  depth=3, slot=6),
    "high": dict(res=224, width=24, depth=4, slot=7),
}


def patch_names():
    csv = os.path.join(os.path.dirname(ROOT), "training_patches_38-cloud_nonempty.csv")
    return [l.strip() for l in open(csv)][1:]


def load_split(res, limit=None):
    """Read RGB + mask, map into the EuroSAT radiometric domain, return arrays."""
    from PIL import Image
    names = patch_names()
    rng = np.random.default_rng(SEED)
    names = list(rng.permutation(names))
    if limit:
        names = names[:limit]
    X = np.zeros((len(names), res, res, 3), np.float32)
    Y = np.zeros((len(names), res, res, 1), np.float32)
    for i, n in enumerate(names):
        for c, band in enumerate(("red", "green", "blue")):
            p = os.path.join(ROOT, f"train_{band}", f"{band}_{n}.TIF")
            X[i, :, :, c] = np.array(Image.open(p).resize((res, res), Image.BILINEAR),
                                     dtype=np.float32)
        g = np.array(Image.open(os.path.join(ROOT, "train_gt", f"gt_{n}.TIF"))
                     .resize((res, res), Image.NEAREST))
        Y[i, :, :, 0] = (g > 127).astype(np.float32)
    flat = X.reshape(-1, 3)
    a = EU_SD / (flat.std(0) + 1e-6)
    b = EU_MU - a * flat.mean(0)
    X = np.clip(X * a + b, 0, 255)
    print(f"38-Cloud -> EuroSAT domain: gain={a.round(5)} offset={b.round(2)}")
    return X, Y, names


def unet(res, width, depth):
    """Small encoder/decoder. Deliberately plain: the rung has to be cheap, and
    a U-Net's skip connections are what matter for boundary quality, not the
    backbone."""
    inp = tf.keras.Input((res, res, 3))
    x = tf.keras.layers.Rescaling(1 / 127.5, offset=-1.0)(inp)
    skips = []
    for d in range(depth):
        x = tf.keras.layers.Conv2D(width * 2 ** d, 3, padding="same", use_bias=False)(x)
        x = tf.keras.layers.BatchNormalization()(x)
        x = tf.keras.layers.ReLU()(x)
        skips.append(x)
        x = tf.keras.layers.MaxPool2D()(x)
    x = tf.keras.layers.Conv2D(width * 2 ** depth, 3, padding="same", activation="relu")(x)
    for d in reversed(range(depth)):
        x = tf.keras.layers.UpSampling2D()(x)
        x = tf.keras.layers.Concatenate()([x, skips[d]])
        x = tf.keras.layers.Conv2D(width * 2 ** d, 3, padding="same", use_bias=False)(x)
        x = tf.keras.layers.BatchNormalization()(x)
        x = tf.keras.layers.ReLU()(x)
    out = tf.keras.layers.Conv2D(1, 1, activation="sigmoid")(x)
    return tf.keras.Model(inp, out)


def augment(x, y, rng):
    """Geometric flips plus the same mild photometric jitter the classifiers
    use -- enough to absorb residual calibration error without erasing the
    brightness cue that separates cloud from bright ground."""
    if rng.random() < 0.5:
        x, y = x[:, ::-1], y[:, ::-1]
    if rng.random() < 0.5:
        x, y = x[::-1], y[::-1]
    g = rng.uniform(0.90, 1.15, 3).astype(np.float32)
    o = rng.uniform(-12, 12, 3).astype(np.float32)
    return np.clip(x * g + o, 0, 255), y


def iou_metric(y_true, y_pred):
    p = tf.cast(y_pred > 0.5, tf.float32)
    inter = tf.reduce_sum(y_true * p)
    union = tf.reduce_sum(tf.cast((y_true + p) > 0, tf.float32))
    return inter / (union + 1e-6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--effort", required=True, choices=list(LADDER))
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--limit", type=int, default=4000)
    ap.add_argument("--out", default="training/out")
    # Same reason as the land-cover ladder: rungs are operating points, and a
    # rung only earns its place if it is well separated from its neighbours in
    # both cost and quality. That needs the curve measured, not assumed.
    ap.add_argument("--res", type=int, help="override input resolution")
    ap.add_argument("--width", type=int, help="override base filter count")
    ap.add_argument("--depth", type=int, help="override number of levels")
    ap.add_argument("--tag", help="output stem suffix instead of the effort name")
    a = ap.parse_args()

    tf.keras.utils.set_random_seed(SEED)
    cfg = dict(LADDER[a.effort])
    for k in ("res", "width", "depth"):
        if getattr(a, k):
            cfg[k] = getattr(a, k)
    LADDER[a.effort] = cfg
    X, Y, names = load_split(cfg["res"], a.limit)
    n_val = int(len(X) * 0.15)
    Xv, Yv, Xt, Yt = X[:n_val], Y[:n_val], X[n_val:], Y[n_val:]
    print(f"effort={a.effort} res={cfg['res']} train={len(Xt)} val={len(Xv)} "
          f"cloud_fraction={Y.mean():.3f}")

    rng = np.random.default_rng(SEED)

    def gen():
        idx = np.arange(len(Xt))
        while True:
            rng.shuffle(idx)
            for i in idx:
                yield augment(Xt[i], Yt[i], rng)

    ds = tf.data.Dataset.from_generator(
        gen, output_signature=(
            tf.TensorSpec((cfg["res"], cfg["res"], 3), tf.float32),
            tf.TensorSpec((cfg["res"], cfg["res"], 1), tf.float32))
    ).batch(a.batch).prefetch(tf.data.AUTOTUNE)

    model = unet(cfg["res"], cfg["width"], cfg["depth"])
    model.compile(optimizer=tf.keras.optimizers.Adam(1e-3),
                  loss="binary_crossentropy", metrics=[iou_metric])
    os.makedirs(a.out, exist_ok=True)
    stem = os.path.join(a.out, f"cloud_{a.tag or a.effort}")
    hist = model.fit(ds, steps_per_epoch=max(1, len(Xt) // a.batch),
                     validation_data=(Xv, Yv), epochs=a.epochs, callbacks=[
                         tf.keras.callbacks.EarlyStopping("val_iou_metric", mode="max",
                                                          patience=6, restore_best_weights=True),
                         tf.keras.callbacks.ReduceLROnPlateau("val_iou_metric", mode="max",
                                                              factor=0.3, patience=3)])
    model.save(f"{stem}.keras")
    best = float(max(hist.history["val_iou_metric"]))
    json.dump({"effort": a.effort, **cfg, "val_iou": best,
               "n_train": len(Xt), "n_val": len(Xv)}, open(f"{stem}.json", "w"), indent=2)
    print(f"wrote {stem}.keras  best val_iou={best:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
