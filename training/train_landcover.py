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


AUG = {
    # Strong: makes the model invariant to radiometry -- but EuroSAT separates
    # SeaLake from Forest almost entirely by absolute brightness (B/G 1.20 vs
    # 1.19, identical texture), so this destroys the only cue that tells water
    # from trees. Measured: Residential 100%, SeaLake 0%. Kept only for
    # ablation.
    "strong": dict(gain=(0.40, 1.8), offset=(-40.0, 90.0), contrast=(0.5, 1.5)),
    # Mild: the pipeline now calibrates radiometry at the source, so the model
    # only has to absorb residual variation -- sun angle, seasonal albedo,
    # calibration error -- without being blinded to brightness.
    "mild":   dict(gain=(0.90, 1.15), offset=(-12.0, 12.0), contrast=(0.85, 1.2)),
}


def photometric(x, strength="mild"):
    """Per-channel gain and offset, plus contrast -- the augmentation the
    original training omitted entirely.

    Ranges come from the measured device distribution rather than from taste:
    tile means span 1..240 and within-tile sigma 8..45, so a model that only
    ever saw EuroSAT's mean ~63 / sigma ~45 has no reason to generalise. The
    offset term is what makes it invariant to processing level (L1C vs L2A),
    which is the single largest effect measured.
    """
    cfg = AUG[strength]
    b = tf.shape(x)[0]
    gain = tf.random.uniform((b, 1, 1, 3), *cfg["gain"])
    offset = tf.random.uniform((b, 1, 1, 3), *cfg["offset"])
    x = x * gain + offset
    # global contrast change on top, applied about the per-image mean
    c = tf.random.uniform((b, 1, 1, 1), *cfg["contrast"])
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
        #
        # Scale inside the model, exactly as the MobileNet path does. Feeding
        # raw 0..255 into the first conv trains by luck: it worked under Keras
        # 3.11 and sat at chance (loss ln(10) = 2.30, never moving) under
        # 3.3.2. Keeping the scaling inside the graph also means the quantised
        # export still takes uint8 straight from the module.
        x = tf.keras.layers.Rescaling(1 / 127.5, offset=-1.0)(inp)
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


def datasets(res, batch, strength="mild"):
    """Build the pipeline from an explicit file list.

    NOT `image_dataset_from_directory(validation_split=..., subset=...)`. Under
    TF 2.17 that returns images and labels that are **not aligned** -- every
    class comes back with the same mean colour, the global corpus mean -- so
    training sits at chance (loss ln(10) = 2.30) while looking perfectly
    healthy. The same code trains to 79% under TF 2.21. Building the pairing
    here makes it explicit, version-independent, and checkable, which the
    assertion below does.
    """
    train_dir = os.path.join(DATA, "training")
    classes = sorted(d for d in os.listdir(train_dir)
                     if os.path.isdir(os.path.join(train_dir, d)))
    paths, labels = [], []
    for i, c in enumerate(classes):
        for f in sorted(os.listdir(os.path.join(train_dir, c))):
            if f.lower().endswith((".jpg", ".jpeg", ".png")):
                paths.append(os.path.join(train_dir, c, f))
                labels.append(i)
    paths, labels = np.array(paths), np.array(labels, np.int32)
    rng = np.random.default_rng(SEED)
    order = rng.permutation(len(paths))
    paths, labels = paths[order], labels[order]
    n_val = int(len(paths) * 0.15)

    def load(path, label):
        img = tf.io.decode_image(tf.io.read_file(path), channels=3,
                                 expand_animations=False)
        img = tf.image.resize(img, (res, res))
        img.set_shape((res, res, 3))
        return img, label

    AUTO = tf.data.AUTOTUNE
    geo = tf.keras.Sequential([
        tf.keras.layers.RandomFlip("horizontal_and_vertical", seed=SEED),
        tf.keras.layers.RandomRotation(0.2, seed=SEED),
        tf.keras.layers.RandomZoom(0.2, seed=SEED),
        tf.keras.layers.RandomTranslation(0.2, 0.2, seed=SEED),
    ])
    tr = (tf.data.Dataset.from_tensor_slices((paths[n_val:], labels[n_val:]))
          .shuffle(4096, seed=SEED).map(load, num_parallel_calls=AUTO).batch(batch)
          .map(lambda x, y: (photometric(geo(x, training=True), strength), y),
               num_parallel_calls=AUTO).prefetch(AUTO))
    # Validation carries the photometric shift too: a model that scores well
    # only on pristine EuroSAT is exactly the failure being fixed, so early
    # stopping must not select for it.
    va = (tf.data.Dataset.from_tensor_slices((paths[:n_val], labels[:n_val]))
          .map(load, num_parallel_calls=AUTO).batch(batch)
          .map(lambda x, y: (photometric(x, strength), y), num_parallel_calls=AUTO)
          .cache().prefetch(AUTO))

    # Pairing check: if images and labels are aligned, per-class mean colour
    # must differ. Identical means across classes is the failure above.
    raw = (tf.data.Dataset.from_tensor_slices((paths, labels))
           .map(load, num_parallel_calls=AUTO).batch(256).take(8))
    sums = {}
    for x, y in raw:
        xn, yn = x.numpy(), y.numpy()
        for k in np.unique(yn):
            sums.setdefault(int(k), []).append(xn[yn == k].reshape(-1, 3).mean(0))
    means = np.array([np.mean(v, axis=0) for v in sums.values()])
    spread = float(means.std(axis=0).mean())
    assert spread > 2.0, (
        f"image/label pairing looks broken: per-class mean colour spread is "
        f"{spread:.2f}, i.e. every class has the same average colour")
    print(f"pairing check ok (per-class colour spread {spread:.1f})")
    return tr, va, classes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--effort", required=True, choices=list(LADDER))
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--aug", default="mild", choices=list(AUG))
    ap.add_argument("--out", default="training/out")
    a = ap.parse_args()

    tf.keras.utils.set_random_seed(SEED)
    cfg = LADDER[a.effort]
    tr, va, classes = datasets(cfg["res"], a.batch, a.aug)
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
