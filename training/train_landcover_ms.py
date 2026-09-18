#!/usr/bin/env python3
"""Train the land-cover rungs on EuroSAT-MS, rendered through the frozen gain.

    training/train_landcover_ms.py --arch custom_conv --gain 0.050 --tag ms_low
    training/train_landcover_ms.py --arch mobilenetv2 --alpha 0.5 --gain 0.050 --tag ms_med
    training/train_landcover_ms.py --arch mobilenetv2 --alpha 1.4 --gain 0.050 --tag ms_high

Why this exists. The deployed classifiers were trained on EuroSAT's RGB JPEGs,
which are `reflectance x 0.093` -- a rendering the payload does not apply. On
the board they scored 39.6 / 56.4 / 57.9% coarse land-cover agreement against
75.67 / 89.98 / 98.17% on EuroSAT, and the top two rungs were indistinguishable
(+1.5 pp against ~3.5 pp noise). EuroSAT-MS is the same 27,000 images as raw
L1C reflectance, so training can render them exactly as the board will.

Three things are fixed by design rather than left to chance:

  * The ARCHITECTURES are the deployed ones, so latency and energy carry over.
    custom_conv is replicated from its flatbuffer: Conv16 > Pool > Conv32 > Pool
    > Conv64 > GAP > Dense10 > Softmax at 224. MobileNetV2 is one flat graph
    (input_tensor=), which is what tfmot can wrap for QAT.
  * NO Rescaling layer. Inputs are mapped to [-1,1] in tf.data, so the
    converter emits input quantisation (2/255, 128) and the module's raw-uint8
    passthrough is exactly right -- the contract that cost one board round-trip
    on segmentation.
  * The SPLIT is the existing one, matched by filename (21,600 / 5,400). A
    validation slice is carved from TRAIN for early stopping; `testing` is
    touched once, at the end, to report.

Two resolutions are worth comparing. EuroSAT is natively 64 px; the deployed
models take 224 and were trained on 64->224 upscales, and matching that blur
was worth ~10 points on device. Training at --res 64 removes the blur question
but changes the architecture's cost; --res 224 keeps cost identical. Default is
224 so the rung stays comparable; --res 64 is the experiment.
"""

import argparse
import json
import os
import sys

import numpy as np
import tensorflow as tf

SEED = 43
NPZ = os.environ.get("EUROSAT_MS", os.path.expanduser(
    "~/dipp-datasets/eurosat-ms/eurosat_ms.npz"))


def load(gain, limit=None, val_frac=0.15, seed=SEED):
    """EuroSAT-MS -> uint8 at the frozen gain, native 64 px, split preserved.

    Kept at 64 px uint8 (330 MB for all 27,000); the resize to the model's
    resolution and the [-1,1] map happen per batch in make_ds(). Pre-rendering
    at 224 px float32 is 11 GB and OOMs the GPU.
    """
    z = np.load(NPZ)
    X, y, is_test, classes = z["X"], z["y"].astype(np.int32), z["is_test"], list(z["classes"])
    # Render EXACTLY as the board's demosaic does: uniform gain, zero offset,
    # clip at 255.
    X = np.clip(X.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    tr_idx = np.flatnonzero(~is_test)
    te_idx = np.flatnonzero(is_test)
    rng = np.random.default_rng(seed)
    tr_idx = rng.permutation(tr_idx)
    if limit:
        # The npz is class-ordered, so a plain head of the test split is one
        # class -- the smoke's holdout must be shuffled too.
        tr_idx = tr_idx[:limit]
        te_idx = rng.permutation(te_idx)[:max(500, limit // 4)]
    n_val = int(len(tr_idx) * val_frac)
    va_idx, tr_idx = tr_idx[:n_val], tr_idx[n_val:]
    print(f"EuroSAT-MS gain={gain}: train {len(tr_idx)}  val {len(va_idx)}  "
          f"test {len(te_idx)}", flush=True)
    return (X[tr_idx], y[tr_idx]), (X[va_idx], y[va_idx]), (X[te_idx], y[te_idx]), classes


def make_ds(X, y, res, batch, train=False):
    """uint8 64 px -> resized float [-1,1] per batch (+ augmentation if train)."""
    AUTO = tf.data.AUTOTUNE
    ds = tf.data.Dataset.from_tensor_slices((X, y))
    if train:
        ds = ds.shuffle(4096, seed=SEED)
    ds = ds.batch(batch)

    def prep(x, y):
        x = tf.cast(x, tf.float32)
        if res != 64:
            x = tf.image.resize(x, (res, res), method="bilinear")
        return x / 127.5 - 1.0, y
    ds = ds.map(prep, num_parallel_calls=AUTO)
    if train:
        ds = ds.map(augment(), num_parallel_calls=AUTO)
    return ds.prefetch(AUTO)


def build_custom_conv(res, n_classes=10):
    """The paper's low rung, recovered from custom_conv_model_landcover.tflite."""
    inp = tf.keras.Input((res, res, 3))
    x = tf.keras.layers.Conv2D(16, 3, padding="same", activation="relu")(inp)
    x = tf.keras.layers.MaxPool2D()(x)
    x = tf.keras.layers.Conv2D(32, 3, padding="same", activation="relu")(x)
    x = tf.keras.layers.MaxPool2D()(x)
    x = tf.keras.layers.Conv2D(64, 3, padding="same", activation="relu")(x)
    x = tf.keras.layers.GlobalAveragePooling2D()(x)
    out = tf.keras.layers.Dense(n_classes, activation="softmax")(x)
    return tf.keras.Model(inp, out)


def build_mobilenet(res, alpha, n_classes=10, weights="imagenet"):
    """One flat graph, no Rescaling -- the shape tfmot can wrap for QAT."""
    inp = tf.keras.Input((res, res, 3))
    base = tf.keras.applications.MobileNetV2(
        input_tensor=inp, alpha=alpha, include_top=False, weights=weights,
        pooling="avg")
    # ImageNet BN moving statistics describe a different input distribution;
    # at the default momentum (0.999) they take thousands of steps to adapt,
    # so inference-mode accuracy sits at chance long after train accuracy is
    # high. 0.99 converges in a few hundred steps and costs nothing later.
    for l in base.layers:
        if isinstance(l, tf.keras.layers.BatchNormalization):
            l.momentum = 0.99
    x = tf.keras.layers.Dropout(0.2)(base.output)
    out = tf.keras.layers.Dense(n_classes, activation="softmax")(x)
    return tf.keras.Model(inp, out)


class Snapshot(tf.keras.callbacks.Callback):
    """Save every epoch's weights, named by val accuracy.

    The rungs are OPERATING POINTS: the medium rung must sit clearly below the
    high one, and a MobileNetV2 alpha=0.5 trained to exhaustion may not. With
    every epoch on disk the medium operating point can be chosen after the fact
    (any snapshot works as --init for the QAT pass) instead of retraining to a
    guessed --target.
    """

    def __init__(self, d):
        super().__init__(); self.d = d; os.makedirs(d, exist_ok=True)

    def on_epoch_end(self, epoch, logs=None):
        acc = (logs or {}).get("val_accuracy", 0.0)
        np.savez(os.path.join(self.d, f"ep{epoch + 1:02d}_val{acc:.4f}.weights.npz"),
                 *self.model.get_weights())


class StopAtTarget(tf.keras.callbacks.Callback):
    """Stop once val_accuracy reaches the operating point asked for."""

    def __init__(self, target):
        super().__init__(); self.target = target

    def on_epoch_end(self, epoch, logs=None):
        acc = (logs or {}).get("val_accuracy", 0.0)
        if self.target and acc >= self.target:
            print(f"  reached target {self.target:.4f} at epoch {epoch + 1} "
                  f"(val_accuracy={acc:.4f}); stopping", flush=True)
            self.model.stop_training = True


def augment(strength="mild"):
    geo = tf.keras.Sequential([
        tf.keras.layers.RandomFlip("horizontal_and_vertical", seed=SEED),
        tf.keras.layers.RandomRotation(0.2, seed=SEED),
        tf.keras.layers.RandomZoom(0.15, seed=SEED),
    ])
    lo, hi = (0.90, 1.15) if strength == "mild" else (0.5, 1.5)
    def f(x, y):
        x = geo(x, training=True)
        g = tf.random.uniform([tf.shape(x)[0], 1, 1, 1], lo, hi, seed=SEED)
        return tf.clip_by_value(x * g, -1.0, 1.0), y
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", choices=["custom_conv", "mobilenetv2"], required=True)
    ap.add_argument("--alpha", type=float, default=1.0)
    ap.add_argument("--res", type=int, default=224)
    ap.add_argument("--gain", type=float, required=True,
                    help="the frozen pipeline rendering, e.g. 0.050")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--limit", type=int, help="training images (smoke tests)")
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--patience", type=int, default=8)
    ap.add_argument("--target", type=float, default=0.0,
                    help="stop once val_accuracy reaches this (an operating point)")
    ap.add_argument("--steps-per-epoch", type=int,
                    help="shorter epochs: --target is checked and a snapshot "
                         "taken more often than once per full pass")
    ap.add_argument("--no-imagenet", action="store_true")
    ap.add_argument("--init", help=".weights.npz to start from (float weights)")
    ap.add_argument("--qat", action="store_true",
                    help="wrap with tfmot fake-quant and fine-tune (use --init, low --lr)")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", default="training/out")
    a = ap.parse_args()
    tf.keras.utils.set_random_seed(SEED)

    (Xt, yt), (Xv, yv), (Xte, yte), classes = load(a.gain, a.limit)
    if a.arch == "custom_conv":
        model = build_custom_conv(a.res, len(classes))
    else:
        model = build_mobilenet(a.res, a.alpha, len(classes),
                                None if a.no_imagenet else "imagenet")
    if a.init:
        z = np.load(a.init)
        w = [z[f"arr_{i}"] for i in range(len(z.files))]
        have = model.get_weights()
        if len(have) != len(w) or any(h.shape != x.shape for h, x in zip(have, w)):
            sys.exit(f"--init {a.init} does not match this graph "
                     f"({len(have)} vs {len(w)} arrays)")
        model.set_weights(w)
        print(f"loaded float weights from {a.init}")
    if a.qat:
        # Fake-quant on the FLOAT graph, so convert_tf29.py can rebuild the same
        # wrapped graph and load these weights (the array count changes).
        import tensorflow_model_optimization as tfmot
        model = tfmot.quantization.keras.quantize_model(model)
    print(f"{a.arch} alpha={a.alpha} res={a.res} qat={a.qat}: "
          f"{model.count_params():,} params", flush=True)

    tr = make_ds(Xt, yt, a.res, a.batch, train=True)
    va = make_ds(Xv, yv, a.res, a.batch)

    model.compile(optimizer=tf.keras.optimizers.Adam(a.lr),
                  loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    os.makedirs(a.out, exist_ok=True)
    stem = os.path.join(a.out, f"landcover_{a.tag}")
    fit_kw = {}
    if a.steps_per_epoch:
        fit_kw["steps_per_epoch"] = a.steps_per_epoch
        tr = tr.repeat()
    hist = model.fit(tr, validation_data=va, epochs=a.epochs, verbose=2, shuffle=False,
                     **fit_kw, callbacks=[Snapshot(os.path.join(a.out, "snap", a.tag)),
                                StopAtTarget(a.target),
                                tf.keras.callbacks.EarlyStopping(
                         "val_accuracy", mode="max", patience=a.patience,
                         restore_best_weights=True),
                                tf.keras.callbacks.ReduceLROnPlateau(
                         "val_accuracy", mode="max", factor=0.3, patience=3)])
    best_val = float(max(hist.history["val_accuracy"]))
    # With --target the run stops at the first epoch reaching it, which is then
    # also the best epoch, so EarlyStopping's restore leaves those weights in.
    # The holdout, once. This is the reported quality number for the rung.
    te_loss, te_acc = model.evaluate(make_ds(Xte, yte, a.res, 128), verbose=0)
    print(f"\nval_accuracy(best)={best_val:.4f}   TEST top-1={te_acc:.4f} "
          f"on {len(yte)} held-out images", flush=True)

    np.savez(f"{stem}.weights.npz", *model.get_weights())
    if not a.qat:
        model.save(f"{stem}.keras")
    json.dump({"arch": a.arch, "alpha": a.alpha, "res": a.res, "gain": a.gain,
               "affine": f"gain:{a.gain}", "dataset": "eurosat-ms",
               "classes": classes, "qat": bool(a.qat), "init": a.init,
               "val_accuracy": best_val,
               "test_accuracy": float(te_acc), "n_train": int(len(yt)),
               "n_test": int(len(yte)), "limit": a.limit, "target": a.target,
               "epochs_run": len(hist.history["val_accuracy"]),
               "steps_per_epoch": a.steps_per_epoch},
              open(f"{stem}.json", "w"), indent=2)
    print(f"wrote {stem}.weights.npz  test_accuracy={te_acc:.4f}")


if __name__ == "__main__":
    sys.exit(main())
