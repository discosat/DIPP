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
DEV_GAIN = np.array([0.005347, 0.003868, 0.003141], np.float32)
DEV_OFFSET = np.array([10.76, 45.23, 60.73], np.float32)
# The device domain itself, pooled over 100 real calibrated tiles from four
# scenes. DEV_GAIN/DEV_OFFSET above map 38-Cloud onto it; CloudSEN12 gets its
# own affine fitted at load time because its reflectance distribution depends
# on which scenes were fetched.
DEV_TARGET_MEAN = [94.4, 104.5, 111.2]
DEV_TARGET_STD = [50.4, 34.8, 29.6]
# The calibration the demosaic module applies on the board (its calib_gain_*
# and calib_offset_* parameters). Sentinel-2 reflectance goes through exactly
# this on the way to the model, so training data should too.
DEV_CALIB_GAIN = np.array([0.040321, 0.031413, 0.025168], np.float32)
DEV_CALIB_OFFSET = np.array([17.270, 36.860, 60.976], np.float32)
# The calibration cannot emit a brighter pixel than this (raw 4095 through the
# affine), so clipping training data at 255 would show the model a brightness
# range the hardware never produces.
DEV_CALIB_MAX = np.array([182.0, 165.0, 164.0], np.float32)
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
    # FIXED transform, not one computed from whatever set happens to be loaded.
    # The previous corpus-adaptive version was unreproducible where it matters:
    # gain over 800 patches is [0.0054 0.0038 0.0031], but over a SINGLE patch
    # it is [0.0072 0.0053 0.0045] -- 33% out -- and a single tile is all the
    # device ever has. The device also applies no such transform at all; it
    # feeds tiles calibrated by the demosaic module. So a model trained that way
    # is trained in one radiometric domain and deployed in another.
    #
    # These constants map 38-Cloud onto the MEASURED device domain (pooled over
    # 100 real calibrated tiles from four scenes: mean 94.4/104.5/111.2, std
    # 50.4/34.8/29.6). After the map 38-Cloud lands at mean 94.4/103.5/111.7,
    # std 50.2/35.0/29.5.
    X = np.clip(X * DEV_GAIN + DEV_OFFSET, 0, 255)
    # The Rescaling layer is gone from the graph, so the mapping to [-1,1] has
    # to happen here instead.
    X = X / 127.5 - 1.0
    print(f"38-Cloud -> device domain: gain={DEV_GAIN} offset={DEV_OFFSET}")
    return X, Y, names


CLOUDSEN12_DIR = os.environ.get("CLOUDSEN12_DIR", os.path.expanduser(
    "~/dipp-datasets/cloudsen12"))

# CloudSEN12 label ids. 0 clear, 1 thick cloud, 2 thin cloud, 3 cloud shadow.
CS12_CLEAR, CS12_THICK, CS12_THIN, CS12_SHADOW = 0, 1, 2, 3


def _valid_window(rgb):
    """Largest all-nonzero rectangle margin, found rather than assumed.

    CloudSEN12 pads 509x509 up to 512x512 and the upstream docs say the zeros
    go on the left and bottom. Trusting that is one transposition away from
    cropping the wrong edge silently, and the cost of being wrong is not a
    cosmetic black bar: the padded pixels carry a LABEL, so a crop that lands on
    them teaches the model that black means whatever padding is labelled --
    almost certainly "clear". A cloud detector that has learned "dark = clear"
    is the failure we already have on device, so this is measured per corpus.
    """
    nz = rgb.any(axis=(0, 3))               # (H, W) over samples and bands
    rows = np.flatnonzero(nz.any(axis=1))
    cols = np.flatnonzero(nz.any(axis=0))
    if not len(rows) or not len(cols):
        return 0, rgb.shape[1], 0, rgb.shape[2]
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def load_cloudsen12(res, limit=None, split="train", thin_is_cloud=True,
                    shadow_is_cloud=False, crops_per_scene=4, seed=SEED,
                    affine="device"):
    """Load packed CloudSEN12 shards, cropped and mapped into the device domain.

    Sentinel-2, where 38-Cloud is Landsat-8: this exists to remove a sensor
    mismatch against the imagery the payload actually sees. Measured on the
    board, the 38-Cloud-trained segmenter marks 0.54% of pixels as cloud on a
    frame named full-clouds, and marks near-identical amounts in all 99 tiles.

    CROPPED, not resized. A device tile is a 224px crop of a larger frame, so
    cropping matches the deployment geometry where a downscaled 512px scene
    would train the model on the wrong ground-sample distance.

    `thin_is_cloud` is the knob that decides whether the effort ladder can earn
    anything. Thick cloud alone is close to a brightness threshold -- that is
    why on 38-Cloud a 3,825-parameter model reaches IoU 0.741 and a 2,207,137
    parameter one only 0.865. Thin cloud needs context, so capacity finally has
    something to buy.
    """
    rgb_p = os.path.join(CLOUDSEN12_DIR, f"{split}_rgb.npy")
    lab_p = os.path.join(CLOUDSEN12_DIR, f"{split}_label.npy")
    if not (os.path.exists(rgb_p) and os.path.exists(lab_p)):
        sys.exit(f"CloudSEN12 shards not found at {CLOUDSEN12_DIR}. Run "
                 f"training/fetch_cloudsen12.py then training/pack_cloudsen12.py")
    R = np.load(rgb_p, mmap_mode="r")
    L = np.load(lab_p, mmap_mode="r")
    n = len(R) if not limit else min(limit, len(R))

    probe = np.asarray(R[:min(32, len(R))])
    r0, r1, c0, c1 = _valid_window(probe)
    if (r1 - r0, c1 - c0) != probe.shape[1:3]:
        print(f"CloudSEN12: padding detected, valid window "
              f"rows {r0}:{r1} cols {c0}:{c1}")
    if r1 - r0 < res or c1 - c0 < res:
        sys.exit(f"res={res} exceeds the valid region {r1-r0}x{c1-c0}")

    rng = np.random.default_rng(seed)
    X = np.zeros((n * crops_per_scene, res, res, 3), np.float32)
    Y = np.zeros((n * crops_per_scene, res, res, 1), np.float32)
    k = 0
    for i in range(n):
        rgb, lab = np.asarray(R[i]), np.asarray(L[i])
        for _ in range(crops_per_scene):
            y0 = int(rng.integers(r0, r1 - res + 1))
            x0 = int(rng.integers(c0, c1 - res + 1))
            X[k] = rgb[y0:y0 + res, x0:x0 + res, :].astype(np.float32)
            m = lab[y0:y0 + res, x0:x0 + res]
            cloud = (m == CS12_THICK)
            if thin_is_cloud:
                cloud |= (m == CS12_THIN)
            if shadow_is_cloud:
                cloud |= (m == CS12_SHADOW)
            Y[k, :, :, 0] = cloud.astype(np.float32)
            k += 1

    # Reflectance -> the device's uint8 domain, then to [-1,1]. Same shape of
    # transform as the 38-Cloud path and for the same reason: the device applies
    # no such mapping, it feeds tiles already calibrated by the demosaic module,
    # so a model trained in a different radiometric domain is deployed into one
    # it has never seen. Fitted by matching mean and std per band against the
    # measured device domain; pack_cloudsen12.py prints the constants.
    gain, off = cs12_affine(X, mode=affine)
    # Clip to the DEVICE ceiling, not 255: the board's calibration tops out at
    # 182/165/164 and cannot present anything brighter.
    ceil = DEV_CALIB_MAX if affine == "device" else 255.0
    X = np.clip(X * gain + off, 0, ceil)
    X = X / 127.5 - 1.0
    print(f"CloudSEN12[{split}]: {n} scenes x {crops_per_scene} crops = {k}, "
          f"cloud_fraction={Y.mean():.3f} "
          f"(thin={'cloud' if thin_is_cloud else 'clear'}, "
          f"shadow={'cloud' if shadow_is_cloud else 'clear'})")
    print(f"  reflectance->device gain={np.round(gain,6)} offset={np.round(off,2)}")
    return X, Y


def cs12_affine(X=None, mode="device"):
    """Reflectance -> the device uint8 domain.

    mode="device" (default) applies the constants the DEVICE ITSELF applies,
    read from demosaic_module.cpp. This is not a fit: it is the transfer
    function the hardware runs on every frame before a model sees a pixel, so
    training through it means training on what will actually be presented.
    Checked against measured board frames it lands thick cloud within 2.3-6.1%
    of where the device puts it (mapped 158.3/141.6/150.0 against measured
    168.5/145.8/146.6).

    mode="fit" solves gain and offset from two content-matched anchors,
    clear->clear and cloud->cloud. It is kept because it is the obvious thing
    to try and because the reason it FAILS is worth preserving: the device
    cloud/clear contrast is 2.71x in red while CloudSEN12 thick+thin/clear is
    1.69x, and no affine reconciles those. Forcing both anchors gives gain
    0.0898 with offset -91.5, so dark pixels clip to zero and the 99.5th
    percentile maps to 337 against a ceiling of 182 -- both ends crushed. The
    contrast mismatch is real content, not error: our clear scenes are Danish
    coast and city with dark water, CloudSEN12 clear is global.

    The thin-cloud case is the one to protect. Under the device mapping thin
    cloud lands at 80.0 red against clear at 86.3 -- still DIMMER, as measured
    in reflectance (1555.7 against 1711.8). That relationship is why this
    dataset can support an effort ladder at all, and the anchored fit distorts
    it.
    """
    if mode == "device":
        return DEV_CALIB_GAIN.copy(), DEV_CALIB_OFFSET.copy()
    if mode.startswith("gain:"):
        # Uniform gain, zero offset: `reflectance x G`. This is the pipeline's
        # frozen rendering (Phase 0 of the completion plan). Uniform because the
        # per-channel offsets in the device affine are what turned dark
        # vegetation blue; the value is chosen by sweep, not inherited.
        g = float(mode.split(":", 1)[1])
        return (np.full(3, g, np.float32), np.zeros(3, np.float32))
    m = X.reshape(-1, 3).mean(0)
    sd = X.reshape(-1, 3).std(0)
    gain = np.where(sd > 0, np.array(DEV_TARGET_STD) / np.maximum(sd, 1e-6), 0.0)
    off = np.array(DEV_TARGET_MEAN) - gain * m
    return gain.astype(np.float32), off.astype(np.float32)


def unet(res, width, depth):
    """Small encoder/decoder. Deliberately plain: the rung has to be cheap, and
    a U-Net's skip connections are what matter for boundary quality, not the
    backbone."""
    # batch_size=1 and no Rescaling layer, both required for the VX delegate.
    # Verified against the two U-Nets that already fly, by op set:
    #   UpSampling2D, batch None, Rescaling -> RESIZE_NEAREST_NEIGHBOR, MUL,
    #                                          PACK, SHAPE, STRIDED_SLICE
    #   Conv2DTranspose, batch None          -> MUL, PACK, SHAPE, STRIDED_SLICE
    #   Conv2DTranspose, batch 1             -> MUL
    #   Conv2DTranspose, batch 1, no Rescale -> nothing the working models lack
    # A dynamic batch makes the transposed convolution compute its output shape
    # at runtime, which is what emits the shape ops. Normalisation moves into
    # the input tensor's quantisation parameters, exactly as the classification
    # models do, so the module still hands over raw uint8.
    inp = tf.keras.Input(shape=(res, res, 3), batch_size=1)
    x = inp
    skips = []
    for d in range(depth):
        x = tf.keras.layers.Conv2D(width * 2 ** d, 3, padding="same", use_bias=False)(x)
        x = tf.keras.layers.BatchNormalization()(x)
        x = tf.keras.layers.ReLU()(x)
        skips.append(x)
        x = tf.keras.layers.MaxPool2D()(x)
    x = tf.keras.layers.Conv2D(width * 2 ** depth, 3, padding="same", activation="relu")(x)
    for d in reversed(range(depth)):
        # Conv2DTranspose, NOT UpSampling2D. UpSampling2D converts to
        # RESIZE_NEAREST_NEIGHBOR, which the i.MX8MP VX delegate gets wrong:
        # measured with inference_diff_run_eval on the board, this model scored
        # avg_error 250.2 out of a uint8 range of 255 against its own CPU
        # reference, i.e. the NPU result is unrelated to the correct one. The
        # two U-Nets that already fly score 0.06 and 1.04, and both upsample
        # with TRANSPOSE_CONV. The delegate is fine; the operator was not.
        x = tf.keras.layers.Conv2DTranspose(width * 2 ** d, 2, strides=2,
                                            padding="same", use_bias=False)(x)
        x = tf.keras.layers.Concatenate()([x, skips[d]])
        x = tf.keras.layers.Conv2D(width * 2 ** d, 3, padding="same", use_bias=False)(x)
        x = tf.keras.layers.BatchNormalization()(x)
        x = tf.keras.layers.ReLU()(x)
    out = tf.keras.layers.Conv2D(1, 1, activation="sigmoid")(x)
    return tf.keras.Model(inp, out)


# MobileNetV2 encoder taps, by output stride. These are the standard U-Net skip
# points used with a MobileNetV2 encoder; naming them explicitly avoids guessing
# at layer indices, which shift with the width multiplier.
MBV2_SKIPS = ["block_1_expand_relu",   # 1/2
              "block_3_expand_relu",   # 1/4
              "block_6_expand_relu",   # 1/8
              "block_13_expand_relu"]  # 1/16


def qat_wrap(model, mode="full"):
    """Fake-quantise the graph for QAT. Shared with convert_tf29.py so the
    conversion rebuilds exactly the wrapped graph the weights came from.

    mode "full"      tfmot.quantize_model -- every layer. On these decoders
                     tfmot's Conv2DTranspose wrapping leaves a runtime
                     TRANSPOSE per stage, splitting the NPU partition; the
                     model then runs on the CPU (1.0x instead of 55x).
    mode "selective" every layer EXCEPT Conv2DTranspose is annotated, so
                     tfmot never wraps those; the converter calibrates them
                     from the representative dataset at export. This keeps
                     TRANSPOSE_CONV (the op set known to work on the VX
                     delegate) while the MobileNet encoder -- where PTQ loses
                     0.07-0.15 IoU -- learns its ranges.
    """
    import tensorflow_model_optimization as tfmot
    q = tfmot.quantization.keras
    if mode == "full":
        return q.quantize_model(model)
    skip = (tf.keras.layers.Conv2DTranspose, tf.keras.layers.InputLayer)

    def annotate(layer):
        if isinstance(layer, skip):
            return layer
        return q.quantize_annotate_layer(layer)
    annotated = tf.keras.models.clone_model(model, clone_function=annotate)
    return q.quantize_apply(annotated)


def unet_mobilenet(res, alpha, dec_max=128):
    """U-Net with a MobileNetV2 encoder -- the paper's high-effort segmentation.

    Built as one flat graph (`input_tensor=`) and with no Rescaling layer, for
    the same two reasons as the classification rungs: tfmot cannot wrap a nested
    Model, and it cannot quantise Rescaling. Normalisation lives in the input
    tensor's quantisation parameters instead, so the module still passes raw
    uint8 through unchanged.
    """
    # batch_size=1, for exactly the reason documented on the plain-conv builder
    # above -- and the omission here is what made every model of this family
    # unrunnable on the NPU. With batch None, Conv2DTranspose has to compute its
    # output shape at run time, which the converter emits as SHAPE ->
    # STRIDED_SLICE -> PACK; the VX delegate cannot verify a graph with dynamic
    # shapes and refuses the whole thing ("Failed to verify graph", node 106).
    # Measured: all four `g` models carry 15 such ops and all four fail on
    # device; all five `p` models, built with batch_size=1, carry none and run.
    inp = tf.keras.Input((res, res, 3), batch_size=1)
    enc = tf.keras.applications.MobileNetV2(
        input_tensor=inp, alpha=alpha, include_top=False, weights="imagenet")
    skips = [enc.get_layer(n).output for n in MBV2_SKIPS]
    x = enc.get_layer("block_16_project").output          # 1/32
    # Conv2DTranspose, not UpSampling2D. UpSampling2D lowers to
    # RESIZE_NEAREST_NEIGHBOR, which the VX delegate computes wrongly -- a
    # trained model using it measured avg_error 250 of 255 against the CPU
    # reference on this board, i.e. the NPU output is unrelated to the CPU's.
    # The deployed segmentation models use TRANSPOSE_CONV and score 0.06 and
    # 1.04, so this is the op set known to work here.
    # Cap the decoder width. Sizing each decoder stage to its skip's channel
    # count follows MobileNetV2-1.4's very wide taps and produced a 24.9 MB
    # flatbuffer -- against 5435 KB for the model this replaces, which would
    # wreck both the size column and the uplink argument the paper makes. The
    # encoder is what carries the representation; the decoder only has to
    # reconstruct a single-channel mask.
    for skip in reversed(skips):
        f = min(int(skip.shape[-1]), dec_max)
        x = tf.keras.layers.Conv2DTranspose(f, 3, strides=2, padding="same",
                                            use_bias=False)(x)
        x = tf.keras.layers.Concatenate()([x, skip])
        x = tf.keras.layers.Conv2D(f, 3, padding="same", use_bias=False)(x)
        x = tf.keras.layers.BatchNormalization()(x)
        x = tf.keras.layers.ReLU()(x)
    x = tf.keras.layers.Conv2DTranspose(min(32, dec_max), 3, strides=2,
                                        padding="same", use_bias=False)(x)
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
    # x is already in [-1,1] -- load_split does the /127.5-1 because the graph
    # has no Rescaling layer. The jitter must therefore be expressed in that
    # domain, not in 0..255. Applying a +/-12 offset to a signal of total width
    # 2 and then clipping at 0 blanked whole channels on roughly half the
    # training samples, and trained the model in a [0,13] domain while
    # validation stayed in [-1,1]. It also made QAT learn an input range of
    # [0,13], which would not match the raw uint8 the module hands over.
    g = rng.uniform(0.90, 1.15, 3).astype(np.float32)
    o = (rng.uniform(-12, 12, 3) / 127.5).astype(np.float32)
    return np.clip(x * g + o, -1.0, 1.0), y


def make_loss(kind, tv_alpha=0.5, tv_beta=0.5, bce_weight=0.5):
    """Loss that matches the metric.

    Training with binary cross-entropy and reporting IoU optimises the wrong
    thing: BCE maximises per-pixel likelihood, which a model can improve while
    overlap stays flat. Dice/Tversky is a smooth surrogate for the overlap
    itself. Tversky also exposes the two error directions separately --
    TP/(TP + a*FP + b*FN) -- so a>b penalises false positives, which are the
    costly direction here because a masked pixel is not downlinked.
    """
    bce = tf.keras.losses.BinaryCrossentropy()

    def tversky(y_true, y_pred):
        yt = tf.reshape(y_true, [-1])
        yp = tf.reshape(y_pred, [-1])
        tp = tf.reduce_sum(yt * yp)
        fp = tf.reduce_sum((1 - yt) * yp)
        fn = tf.reduce_sum(yt * (1 - yp))
        return 1.0 - (tp + 1e-6) / (tp + tv_alpha * fp + tv_beta * fn + 1e-6)

    if kind == "bce":
        return bce
    if kind in ("dice", "tversky"):
        return tversky
    if kind in ("bcedice", "bcetversky"):
        return lambda yt, yp: bce_weight * bce(yt, yp) + (1 - bce_weight) * tversky(yt, yp)
    raise ValueError(kind)


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
    ap.add_argument("--loss", default="bce",
                    choices=["bce", "dice", "tversky", "bcedice", "bcetversky"])
    ap.add_argument("--tv-alpha", type=float, default=0.5, help="false-positive weight")
    ap.add_argument("--tv-beta", type=float, default=0.5, help="false-negative weight")
    ap.add_argument("--dec-max", type=int, default=128,
                    help="cap on decoder filters per stage")
    ap.add_argument("--out", default="training/out")
    # Same reason as the land-cover ladder: rungs are operating points, and a
    # rung only earns its place if it is well separated from its neighbours in
    # both cost and quality. That needs the curve measured, not assumed.
    ap.add_argument("--res", type=int, help="override input resolution")
    ap.add_argument("--width", type=int, help="override base filter count")
    ap.add_argument("--depth", type=int, help="override number of levels")
    ap.add_argument("--tag", help="output stem suffix instead of the effort name")
    ap.add_argument("--backbone", choices=["plain", "mobilenetv2"], default="plain",
                    help="'mobilenetv2' matches the paper's high-effort model")
    # Quantisation-aware training. Post-training quantisation is not a small
    # correction on these models and it is not even monotone in capacity: on the
    # held-out scenes alpha=0.35 lost 0.000 IoU, alpha=1.4 lost 0.089 and
    # alpha=1.0 lost 0.374. That makes selecting a rung on float accuracy
    # invalid, because the ranking that ships is the int8 one -- the best float
    # model here was the worst deployed model.
    ap.add_argument("--qat", nargs="?", const="full", default=None,
                    choices=["full", "selective"],
                    help="quantisation-aware training: 'full' (every layer) or "
                         "'selective' (all but Conv2DTranspose, NPU-safe)")
    ap.add_argument("--init", help="float .weights.npz to start QAT from")
    ap.add_argument("--lr", type=float, default=1e-3)
    # Patience 6 stopped three configs at 10-12 epochs while their neighbours
    # ran 29-32, which made alpha=0.5 look worse than alpha=0.35 when it was
    # simply undertrained. QAT plateaus are bumpier than float ones.
    ap.add_argument("--patience", type=int, default=12)
    ap.add_argument("--alpha", type=float, default=1.4,
                    help="width multiplier when --backbone mobilenetv2")
    # 38-Cloud is Landsat-8 and the payload flies Sentinel-2. That mismatch is
    # measurable on the board: the 38-Cloud segmenter marks 0.54% of pixels as
    # cloud on a full-cloud frame, near-identically in all 99 tiles.
    ap.add_argument("--dataset", choices=["38cloud", "cloudsen12"],
                    default="38cloud")
    # Whether thin cloud counts as cloud decides whether the effort ladder can
    # earn anything: thick-only detection is close to a brightness threshold,
    # which is why a 3,825-parameter model reached IoU 0.741 on 38-Cloud and a
    # 2,207,137-parameter one only 0.865.
    ap.add_argument("--thin-clear", action="store_true",
                    help="treat thin cloud as CLEAR (default: cloud)")
    ap.add_argument("--shadow-cloud", action="store_true",
                    help="treat cloud shadow as cloud (default: clear)")
    ap.add_argument("--affine", default="device",
                    help="reflectance->uint8 map: 'device' (old affine), 'fit' "
                         "(class anchors), or 'gain:<G>' uniform gain, zero "
                         "offset -- the frozen pipeline rendering")
    ap.add_argument("--crops", type=int, default=4,
                    help="random crops taken per CloudSEN12 scene")
    a = ap.parse_args()

    tf.keras.utils.set_random_seed(SEED)
    cfg = dict(LADDER[a.effort])
    for k in ("res", "width", "depth"):
        if getattr(a, k):
            cfg[k] = getattr(a, k)
    LADDER[a.effort] = cfg
    if a.dataset == "cloudsen12":
        # CloudSEN12 ships its own train/validation/test split over 2,000
        # distinct ROIs, so the scene-disjoint machinery below is unnecessary
        # and would be worse: re-splitting a corpus that is already split by ROI
        # can only make the validation set dirtier, never cleaner.
        Xt, Yt = load_cloudsen12(cfg["res"], a.limit, "train",
                                 thin_is_cloud=not a.thin_clear,
                                 shadow_is_cloud=a.shadow_cloud,
                                 crops_per_scene=a.crops, affine=a.affine)
        Xv, Yv = load_cloudsen12(cfg["res"], None, "validation",
                                 thin_is_cloud=not a.thin_clear,
                                 shadow_is_cloud=a.shadow_cloud,
                                 crops_per_scene=1, affine=a.affine)
        print(f"effort={a.effort} res={cfg['res']} train={len(Xt)} val={len(Xv)} "
              f"(CloudSEN12 official split)")
    else:
        X, Y, names = load_split(cfg["res"], a.limit)
        # Split by SCENE, not by patch. 38-Cloud patches are 384x384 crops of
        # a few Landsat scenes, so neighbouring patches overlap in content and a
        # random split leaks the validation set into training -- which is how a
        # model can report val_iou 0.865 and then measure 0.538 held out.
        scenes = np.array([n.split("_by_")[1].split("_", 1)[1] for n in names])
        uniq = sorted(set(scenes))
        rng_s = np.random.default_rng(SEED)
        val_scenes = set(rng_s.permutation(uniq)[:max(1, len(uniq) // 5)])
        is_val = np.array([sc in val_scenes for sc in scenes])
        Xv, Yv, Xt, Yt = X[is_val], Y[is_val], X[~is_val], Y[~is_val]
        print(f"scene-disjoint split: {len(uniq)} scenes, "
              f"{len(val_scenes)} held out ({sorted(val_scenes)[:2]}...)")
        print(f"effort={a.effort} res={cfg['res']} train={len(Xt)} "
              f"val={len(Xv)} cloud_fraction={Y.mean():.3f}")

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

    if a.backbone == "mobilenetv2":
        model = unet_mobilenet(cfg["res"], a.alpha, a.dec_max)
        cfg = dict(cfg, backbone="mobilenetv2", alpha=a.alpha, dec_max=a.dec_max)
    else:
        model = unet(cfg["res"], cfg["width"], cfg["depth"])
    # QAT starts from trained float weights: it only has to adapt to the
    # quantiser, not learn the task, so it converges in a few epochs.
    if a.init:
        z = np.load(a.init)
        w = [z[f"arr_{i}"] for i in range(len(z.files))]
        have = model.get_weights()
        if len(have) == len(w) and all(h.shape == x.shape for h, x in zip(have, w)):
            model.set_weights(w)
            print(f"loaded float weights from {a.init}")
        else:
            print(f"WARNING: {a.init} does not match this graph "
                  f"({len(have)} vs {len(w)} arrays); training from scratch",
                  file=sys.stderr)
    if a.qat:
        model = qat_wrap(model, a.qat)
        print(f"QAT wrapped ({a.qat}): {model.count_params():,} params")

    model.compile(optimizer=tf.keras.optimizers.Adam(a.lr),
                  loss=make_loss(a.loss, a.tv_alpha, a.tv_beta),
                  metrics=[iou_metric])
    os.makedirs(a.out, exist_ok=True)
    stem = os.path.join(a.out, f"cloud_{a.tag or a.effort}")
    hist = model.fit(ds, steps_per_epoch=max(1, len(Xt) // a.batch),
                     validation_data=(Xv, Yv), epochs=a.epochs, callbacks=[
                         tf.keras.callbacks.EarlyStopping("val_iou_metric", mode="max",
                                                          patience=a.patience,
                                                          restore_best_weights=True),
                         tf.keras.callbacks.ReduceLROnPlateau("val_iou_metric", mode="max",
                                                              factor=0.3, patience=3)])
    best = float(max(hist.history["val_iou_metric"]))
    # Save weights directly rather than only a .keras: a QAT-wrapped model needs
    # tfmot's custom objects to reload, and the conversion step rebuilds the
    # same graph and loads these arrays instead.
    np.savez(f"{stem}.weights.npz", *model.get_weights())
    if not a.qat:
        model.save(f"{stem}.keras")
    json.dump({"effort": a.effort, **cfg, "val_iou": best, "qat": a.qat or False,
           "dataset": a.dataset, "affine": a.affine,
           "thin_is_cloud": not a.thin_clear,
           "shadow_is_cloud": a.shadow_cloud,
               "n_train": len(Xt), "n_val": len(Xv)}, open(f"{stem}.json", "w"), indent=2)
    print(f"wrote {stem}.weights.npz  best val_iou={best:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
