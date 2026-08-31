#!/usr/bin/env python3
"""Train the EuroSAT land-cover effort ladder.

    training/train_landcover.py --effort low|medium|high [--epochs N]

Why this exists: the shipped models score 75.7% / 48.9% / 65.9% on EuroSAT for
low / medium / high effort -- the ladder is inverted, so spending 6.6x the
energy buys 9.8 points *less* accuracy -- and all three collapse to near zero on
the imagery the pipeline actually produces.

The cause is measurable and singular. The original training used
RandomFlip / RandomRotation / RandomZoom / RandomTranslation: geometric
augmentation only, no photometric augmentation at all. So the models learned
absolute band values rather than spectral structure. Subtracting a per-channel
dark-object offset from EuroSAT -- roughly the difference between a
top-of-atmosphere and a surface-reflectance product -- drops accuracy from
87.3% to 0.0% on SeaLake and 95.3% to 1.3% on Residential. Our frames are
atmospherically corrected; EuroSAT is not; nothing in the models tolerates the
difference.

So the fix is photometric augmentation spanning the radiometry the device
actually produces, measured over all 495 tiles of the five real frames
(analysis/stage4.5-models/device_stats.json): per-tile channel means from 1 to
240 and within-tile sigma from 8 to 45, against EuroSAT's mean ~63 and sigma
~45.

Two further rules the original violated:

  * Validation comes from a split of `training/`, never from `testing/`. The
    original built val_ds from TRAIN_DIR, so no held-out number has ever
    existed for these models. `testing/` is touched once, at the end.
  * Effort scales by input resolution as well as capacity. The tiler keeps
    emitting 224x224 -- changing tile size would only produce more tiles and
    leave total work unchanged -- so a cheap model takes a downsampled tile
    covering the same ground.
"""

import argparse
import json
import os
import sys

import numpy as np
import tensorflow as tf

DATA = os.environ.get(
    "EUROSAT_DIR",
    os.path.expanduser("~/dipp1-experiments/tensorflow-conversion/EuroSat-working"))
SEED = 43

# resolution, alpha (None = the small custom CNN), and the config slot it fills
LADDER = {
    "low":    dict(res=64,  alpha=None, slot=3),
    "medium": dict(res=128, alpha=0.35, slot=4),
    "high":   dict(res=224, alpha=1.4,  slot=5),
}


def photometric(x):
    """Per-channel gain and offset, plus contrast -- the augmentation the
    original training omitted entirely.

    Ranges come from the measured device distribution rather than from taste:
    tile means span 1..240 and within-tile sigma 8..45, so a model that only
    ever saw EuroSAT's mean ~63 / sigma ~45 has no reason to generalise. The
    offset term is what makes it invariant to processing level (L1C vs L2A),
    which is the single largest effect measured.
    """
    b = tf.shape(x)[0]
    # An atmospheric forward/inverse model is affine per band: a top-of-
    # atmosphere product is roughly 0.5 x surface reflectance plus a
    # blue-weighted path-radiance offset. Solving that from the measured class
    # means gives TOA ~ 0.52 x L2A + [46, 58, 74], so the offset range has to
    # reach ~+75 in blue and the gain has to reach ~0.5. A symmetric +/-55
    # range does not cover it, which is why water stayed misclassified.
    gain = tf.random.uniform((b, 1, 1, 3), 0.40, 1.8)
    offset = tf.random.uniform((b, 1, 1, 3), -40.0, 90.0)
    x = x * gain + offset
    # global contrast change on top, applied about the per-image mean
    c = tf.random.uniform((b, 1, 1, 1), 0.5, 1.5)
    mean = tf.reduce_mean(x, axis=[1, 2, 3], keepdims=True)
    x = (x - mean) * c + mean
    return tf.clip_by_value(x, 0.0, 255.0)


def build(effort, n_classes=10):
    cfg = LADDER[effort]
    res, alpha = cfg["res"], cfg["alpha"]
    inp = tf.keras.Input((res, res, 3))
    if alpha is None:
        # Deliberately small: the low rung has to be genuinely cheap, or there
        # is nothing for the scheduler to trade against.
        x = inp
        for f in (16, 32, 64, 128):
            x = tf.keras.layers.Conv2D(f, 3, padding="same", use_bias=False)(x)
            x = tf.keras.layers.BatchNormalization()(x)
            x = tf.keras.layers.ReLU()(x)
            x = tf.keras.layers.MaxPool2D()(x)
        x = tf.keras.layers.GlobalAveragePooling2D()(x)
        x = tf.keras.layers.Dropout(0.2)(x)
        out = tf.keras.layers.Dense(n_classes)(x)
        return tf.keras.Model(inp, out)

    # MobileNetV2 expects [-1,1]; our tensor arrives as 0..255 so that the
    # quantised model can take raw uint8 exactly as the module hands it over.
    scaled = tf.keras.layers.Rescaling(1 / 127.5, offset=-1.0)(inp)
    base = tf.keras.applications.MobileNetV2(
        input_shape=(res, res, 3), alpha=alpha, include_top=False,
        weights="imagenet", pooling="avg")
    x = base(scaled)
    x = tf.keras.layers.Dropout(0.2)(x)
    out = tf.keras.layers.Dense(n_classes)(x)
    return tf.keras.Model(inp, out)


def datasets(res, batch):
    train_dir = os.path.join(DATA, "training")
    common = dict(labels="inferred", label_mode="int", image_size=(res, res),
                  batch_size=batch, seed=SEED)
    tr = tf.keras.utils.image_dataset_from_directory(
        train_dir, validation_split=0.15, subset="training", **common)
    va = tf.keras.utils.image_dataset_from_directory(
        train_dir, validation_split=0.15, subset="validation", **common)
    classes = tr.class_names
    geo = tf.keras.Sequential([
        tf.keras.layers.RandomFlip("horizontal_and_vertical", seed=SEED),
        tf.keras.layers.RandomRotation(0.2, seed=SEED),
        tf.keras.layers.RandomZoom(0.2, seed=SEED),
        tf.keras.layers.RandomTranslation(0.2, 0.2, seed=SEED),
    ])
    AUTO = tf.data.AUTOTUNE
    tr = tr.map(lambda x, y: (photometric(geo(x, training=True)), y),
                num_parallel_calls=AUTO).prefetch(AUTO)
    # Validation gets the photometric shift too: a model that scores well only
    # on pristine EuroSAT is exactly the failure being fixed, so early stopping
    # must not select for it.
    va = va.map(lambda x, y: (photometric(x), y), num_parallel_calls=AUTO).cache().prefetch(AUTO)
    return tr, va, classes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--effort", required=True, choices=list(LADDER))
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--out", default="training/out")
    a = ap.parse_args()

    tf.keras.utils.set_random_seed(SEED)
    cfg = LADDER[a.effort]
    tr, va, classes = datasets(cfg["res"], a.batch)
    print(f"effort={a.effort} res={cfg['res']} alpha={cfg['alpha']} "
          f"classes={classes}")

    model = build(a.effort, len(classes))
    model.compile(optimizer=tf.keras.optimizers.Adam(1e-3),
                  loss=tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True),
                  metrics=["accuracy"])
    os.makedirs(a.out, exist_ok=True)
    stem = os.path.join(a.out, f"landcover_{a.effort}")
    hist = model.fit(tr, validation_data=va, epochs=a.epochs, callbacks=[
        tf.keras.callbacks.EarlyStopping("val_accuracy", patience=6,
                                         restore_best_weights=True),
        tf.keras.callbacks.ReduceLROnPlateau("val_accuracy", factor=0.3, patience=3),
    ])
    model.save(f"{stem}.keras")
    json.dump({"effort": a.effort, **cfg, "classes": classes,
               "val_accuracy": float(max(hist.history["val_accuracy"]))},
              open(f"{stem}.json", "w"), indent=2)
    print(f"wrote {stem}.keras  best val_accuracy="
          f"{max(hist.history['val_accuracy']):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
