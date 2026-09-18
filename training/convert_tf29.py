#!/usr/bin/env python3
"""Quantise to a TFLite the board can actually execute.

    training/convert_tf29.py training/out/landcover_sm_cnn064.weights.npz

Run this under TF 2.9.1 -- the same version the board carries
(`lib/tensorflow-imx` is 2.9.1). Models converted by TF 2.17 load, allocate and
run under 2.9.1 without any error, and return the *same class for every input*.
That was reproduced off-board: 99 tiles of a half-sea frame give one distinct
prediction under 2.9.1 and seven under the modern runtime, while the original
models give seven under both. Nothing in the file explains it -- op codes and
versions match the working models exactly, and so does the quantisation scheme
(int8 weights, uint8 I/O, per-channel).

So the architecture is rebuilt here from the same builder the training script
uses, the trained weights are loaded from the .npz, and the conversion is done
by 2.9's converter. Weight order is positional, which is safe only because both
sides call the same `build()`.
"""
import argparse, json, os, sys
import numpy as np
import tensorflow as tf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def representative(res, data_dir, n=300, seed=43, pm1=False):
    """Calibrate on the training split, resized as the module resizes.

    `pm1` must match the domain the model was TRAINED on. Models built without
    a Rescaling layer (the QAT classification rungs, and the cloud rungs since
    the decoder rewrite) expect [-1,1], with the mapping carried by the input
    tensor's quantisation parameters. Calibrating those with 0..255 data yields
    input quantisation (1.0, 0) -- the model is then fed raw 0..255 where it
    expects [-1,1], and the output is unrelated to the input. That failure is
    silent: the conversion succeeds and only the accuracy collapses.
    """
    import glob
    files = sorted(glob.glob(os.path.join(data_dir, "training", "*", "*.jpg")))
    rng = np.random.default_rng(seed)
    files = [files[i] for i in rng.permutation(len(files))[:n]]
    for f in files:
        img = tf.io.decode_image(tf.io.read_file(f), channels=3,
                                 expand_animations=False)
        img = tf.image.resize(img, (res, res))
        img = tf.cast(tf.expand_dims(img, 0), tf.float32)
        yield [img / 127.5 - 1.0 if pm1 else img]


def representative_cloud(res, n=300, seed=43):
    """Calibrate a cloud model on 38-Cloud, in the domain it was trained in.

    The generic `representative()` above globs `training/*/*.jpg` under
    EUROSAT_DIR -- EuroSAT land-cover RGB. Every cloud model exported so far was
    calibrated on that, which is the wrong domain twice over: wrong content
    (land cover, not cloud) and wrong radiometry (EuroSAT pixel statistics, not
    the device-domain affine 38-Cloud is mapped through during training). PTQ
    ranges taken from the wrong distribution are exactly the silent failure the
    --pm1 note above describes.

    This reuses train_cloud's own loader, so the transform chain is the same
    object rather than a copy that can drift: TIF bands -> bilinear resize ->
    clip(x*DEV_GAIN + DEV_OFFSET, 0, 255) -> /127.5 - 1.
    """
    import train_cloud as tc
    X, _, _ = tc.load_split(res, limit=n)
    rng = np.random.default_rng(seed)
    for i in rng.permutation(len(X))[:n]:
        yield [X[i:i + 1].astype(np.float32)]


def representative_cs12(res, affine, n=200, seed=43):
    """Calibrate on CloudSEN12 rendered through the MODEL'S OWN affine.

    Needed for any model trained at a gain other than the old device affine:
    representative_device() renders board tiles through calibration.json, i.e.
    the OLD gain and offsets, which is the wrong distribution for a model trained
    at `gain:0.060`. The full-range ramp is kept so the input quantisation still
    lands on the module's passthrough contract (scale 2/255, zp 128).
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import train_cloud as tc
    # Prefer train; fall back to validation where only the held-out splits are
    # present (the conversion host has validation/test, the cluster has train).
    # Calibration only sets activation ranges from ~120 unlabelled samples, so
    # using validation does not tune anything toward a label -- but test stays
    # untouched regardless.
    split = "train" if os.path.exists(os.path.join(tc.CLOUDSEN12_DIR,
                                                   "train_rgb.npy")) else "validation"
    X, _ = tc.load_cloudsen12(res, limit=max(8, n // 3), split=split,
                              crops_per_scene=3, affine=affine)
    print(f"cs12 calibration from split={split}")
    rng = np.random.default_rng(seed)
    ramp = (np.linspace(0, 255, res * res * 3, dtype=np.float32)
            .reshape(1, res, res, 3) / 127.5 - 1.0)
    yield [ramp]
    for i in rng.permutation(len(X))[:n]:
        yield [X[i:i + 1].astype(np.float32)]


def representative_device(res, n=200, seed=43):
    """Calibrate on REAL DEVICE TILES -- the exact deployment distribution.

    Preferred for cloud models over representative_cloud(): that one reads
    38-Cloud, which is Landsat-8, and calibrating a Sentinel-2 model on Landsat
    reintroduces the sensor mismatch the CloudSEN12 switch exists to remove. It
    also needs the training corpus present, which the conversion host may not
    have.

    These tiles are what the payload actually produces: raw 12-bit Bayer through
    demosaic_module.cpp's own calibration, exactly as the module feeds the
    model. Nothing is closer to the deployment domain than the deployment
    domain.
    """
    import glob
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "analysis"))
    from device_labelled_eval import calibrated_tiles, DATA        # noqa: E402
    scenes = sorted(glob.glob(os.path.join(DATA, "*.bayerRG")))
    if not scenes:
        sys.exit(f"no device frames under {DATA}")
    tiles = []
    for sc in scenes:
        tiles += calibrated_tiles(sc)
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(tiles))[:n]
    print(f"calibrating on {len(idx)} real device tiles from {len(scenes)} scenes")

    # The input range MUST come out as [-1, 1] exactly, giving quantisation
    # scale 2/255 and zero point 128. This is a hard contract with the module,
    # not a preference: tflite_binary_segmentation writes the raw uint8 tile
    # straight into the input tensor, so the model sees (v - zp) * scale, and
    # only zp=128 / scale=2/255 makes that equal the v/127.5 - 1 the model was
    # trained on.
    #
    # Calibrating on device tiles alone breaks it. They span only [-0.87, 0.43]
    # -- the board's calibration cannot emit brighter than 182 -- so the
    # converter derives scale 0.005075 / zp 171, and under passthrough a pixel
    # of 182 arrives as 0.056 instead of 0.427. The entire range collapses into
    # a narrow band, nothing crosses a 0.5 threshold, and the board masked 0.00%
    # of a frame the host masked 96.58% of. A tighter range is better numerically
    # and fatal here.
    #
    # One synthetic full-range ramp pins the endpoints while the other n samples
    # keep the interior activation statistics honest.
    ramp = (np.linspace(0, 255, res * res * 3, dtype=np.float32)
            .reshape(1, res, res, 3) / 127.5 - 1.0)
    yield [ramp]
    for i in idx:
        t = tiles[i].astype(np.float32)
        if t.shape[0] != res:
            t = np.array(tf.image.resize(t, (res, res)))
        yield [(t[None] / 127.5 - 1.0).astype(np.float32)]


def representative_eurosat_ms(res, gain, n=300, seed=43):
    """Calibrate on EuroSAT-MS rendered through the model's own gain.

    Same rendering as train_landcover_ms.load(): clip(reflectance * gain, 0,
    255), then [-1,1]. A full-range ramp goes first for the same reason as in
    representative_device(): the rendered corpus does not reach 255, and the
    input quantisation must still come out as (2/255, 128) or the module's raw
    uint8 passthrough feeds the model the wrong domain.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import train_landcover_ms as tm
    z = np.load(tm.NPZ)
    idx = np.flatnonzero(~z["is_test"])
    idx = np.random.default_rng(seed).permutation(idx)[:n]
    print(f"calibrating on {len(idx)} EuroSAT-MS training images at gain {gain}")
    ramp = (np.linspace(0, 255, res * res * 3, dtype=np.float32)
            .reshape(1, res, res, 3) / 127.5 - 1.0)
    yield [ramp]
    for i in idx:
        u8 = np.clip(z["X"][i].astype(np.float32) * gain, 0, 255)
        if res != u8.shape[0]:
            u8 = np.array(tf.image.resize(u8, (res, res)))
        yield [(u8[None] / 127.5 - 1.0).astype(np.float32)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("weights_npz")
    ap.add_argument("-o", "--out")
    ap.add_argument("--samples", type=int, default=300)
    # Calibration draw is a real source of variance, not a detail: the same
    # a=0.35 weights quantised losslessly at 96px and 224px but lost eleven
    # points at 128px. Exposing the seed makes that measurable instead of
    # something that silently differs between exports.
    ap.add_argument("--calib-seed", type=int, default=43)
    ap.add_argument("--calib-affine", default=None,
                    help="with --calib cs12: the affine the model trained through, e.g. gain:0.060")
    ap.add_argument("--calib", choices=["auto", "device", "cloud", "cs12", "eurosat", "eurosat-ms"],
                    default="auto",
                    help="representative dataset: 'device' uses real board "
                         "tiles, which is the deployment distribution")
    ap.add_argument("--pm1", action="store_true",
                    help="model expects [-1,1] input (no Rescaling layer in graph)")
    ap.add_argument("--data", default=os.environ.get(
        "EUROSAT_DIR", os.path.expanduser(
            "~/dipp1-experiments/tensorflow-conversion/EuroSat-working")))
    a = ap.parse_args()

    meta_path = a.weights_npz.replace(".weights.npz", ".json")
    meta = json.load(open(meta_path))
    res, alpha = int(meta["res"]), meta.get("alpha")
    kind = "cloud" if "cloud" in os.path.basename(a.weights_npz) else "landcover"

    if kind == "landcover" and meta.get("dataset") == "eurosat-ms":
        # The retrained rungs are flat graphs with no Rescaling layer; the old
        # nested builder would fail the weight-count check below.
        import train_landcover_ms as tm
        n_cls = len(meta.get("classes", [0] * 10))
        if meta["arch"] == "custom_conv":
            model = tm.build_custom_conv(res, n_cls)
        else:
            model = tm.build_mobilenet(res, float(alpha), n_cls, weights=None)
    elif kind == "landcover":
        import train_landcover as tl
        tl.LADDER["medium"] = dict(res=res, alpha=alpha,
                                   slot=meta.get("slot", 4))
        model = tl.build("medium", len(meta.get("classes", [0] * 10)))
    else:
        import train_cloud as tc
        # The cloud ladder has two architectures now: the plain U-Net and the
        # MobileNetV2-encoder one. Rebuilding the wrong shape silently fails the
        # weight-count check, so pick from the metadata.
        if meta.get("backbone") == "mobilenetv2":
            model = tc.unet_mobilenet(res, float(meta["alpha"]),
                                      int(meta.get("dec_max", 128)))
        else:
            model = tc.unet(res, int(meta["width"]), int(meta["depth"]))

    # A QAT model must be rebuilt wrapped before its weights will load: the
    # fake-quant nodes carry their own variables, so the array count differs
    # from the float graph.
    qat_mode = meta.get("qat")
    if qat_mode:
        if kind == "cloud":
            model = tc.qat_wrap(model, "full" if qat_mode is True else qat_mode)
        else:
            import tensorflow_model_optimization as tfmot
            model = tfmot.quantization.keras.quantize_model(model)
        print(f"rebuilt QAT-wrapped graph ({qat_mode})")

    z = np.load(a.weights_npz)
    weights = [z[f"arr_{i}"] for i in range(len(z.files))]
    have = model.get_weights()
    if len(have) != len(weights):
        sys.exit(f"weight count mismatch: model {len(have)} vs npz {len(weights)}")
    for i, (h, w) in enumerate(zip(have, weights)):
        if h.shape != w.shape:
            sys.exit(f"weight {i} shape mismatch: model {h.shape} vs npz {w.shape}")
    model.set_weights(weights)

    conv = tf.lite.TFLiteConverter.from_keras_model(model)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    if qat_mode and qat_mode != "selective":
        # QAT models already carry activation ranges in their fake-quant nodes;
        # supplying a representative dataset would override them with ranges
        # calibrated on different data.
        print("QAT model: converting from learned ranges, no calibration set")
    else:
        if qat_mode == "selective":
            print("selective QAT: learned ranges where wrapped, calibration for "
                  "the Conv2DTranspose layers")
        pick = a.calib
        if pick == "auto":
            # A CloudSEN12-trained model must not be calibrated on 38-Cloud.
            pick = "device" if meta.get("dataset") == "cloudsen12" else (
                "eurosat-ms" if meta.get("dataset") == "eurosat-ms" else
                "cloud" if kind == "cloud" else "eurosat")
        print(f"calibration set: {pick}")
        if pick == "eurosat-ms":
            conv.representative_dataset = lambda: representative_eurosat_ms(
                res, float(meta["gain"]), a.samples, a.calib_seed)
        elif pick == "cs12":
            aff = a.calib_affine or meta.get("affine")
            if not aff:
                sys.exit("--calib cs12 needs --calib-affine (or 'affine' in the json)")
            conv.representative_dataset = lambda: representative_cs12(
                res, aff, a.samples, a.calib_seed)
        elif pick == "device":
            conv.representative_dataset = lambda: representative_device(
                res, a.samples, a.calib_seed)
        elif pick == "cloud":
            conv.representative_dataset = lambda: representative_cloud(
                res, a.samples, a.calib_seed)
        else:
            conv.representative_dataset = lambda: representative(
                res, a.data, a.samples, a.calib_seed, a.pm1)
    if not qat_mode or qat_mode == "selective":
        # Forcing full-integer mode demands a representative dataset. A QAT
        # model already carries its ranges in fake-quant nodes, so this is both
        # unnecessary and harmful there -- TFLite derives int8 weights and
        # activations from the learned ranges instead.
        conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = tf.uint8
    conv.inference_output_type = tf.uint8
    blob = conv.convert()

    out = a.out or a.weights_npz.replace(".weights.npz", ".tflite")
    open(out, "wb").write(blob)
    it = tf.lite.Interpreter(model_content=blob); it.allocate_tensors()
    di, do = it.get_input_details()[0], it.get_output_details()[0]
    print(f"wrote {out}  ({len(blob):,} B, TF {tf.__version__})")
    print(f"  in  {di['shape']} {di['dtype'].__name__} q={di['quantization']}")
    print(f"  out {do['shape']} {do['dtype'].__name__} q={do['quantization']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
