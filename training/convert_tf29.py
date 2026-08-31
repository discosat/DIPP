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


def representative(res, data_dir, n=300, seed=43):
    """Calibrate on the training split, resized as the module resizes."""
    import glob
    files = sorted(glob.glob(os.path.join(data_dir, "training", "*", "*.jpg")))
    rng = np.random.default_rng(seed)
    files = [files[i] for i in rng.permutation(len(files))[:n]]
    for f in files:
        img = tf.io.decode_image(tf.io.read_file(f), channels=3,
                                 expand_animations=False)
        img = tf.image.resize(img, (res, res))
        yield [tf.cast(tf.expand_dims(img, 0), tf.float32)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("weights_npz")
    ap.add_argument("-o", "--out")
    ap.add_argument("--samples", type=int, default=300)
    ap.add_argument("--data", default=os.environ.get(
        "EUROSAT_DIR", os.path.expanduser(
            "~/dipp1-experiments/tensorflow-conversion/EuroSat-working")))
    a = ap.parse_args()

    meta_path = a.weights_npz.replace(".weights.npz", ".json")
    meta = json.load(open(meta_path))
    res, alpha = int(meta["res"]), meta.get("alpha")
    kind = "cloud" if "cloud" in os.path.basename(a.weights_npz) else "landcover"

    if kind == "landcover":
        import train_landcover as tl
        tl.LADDER["medium"] = dict(res=res, alpha=alpha,
                                   slot=meta.get("slot", 4))
        model = tl.build("medium", len(meta.get("classes", [0] * 10)))
    else:
        import train_cloud as tc
        model = tc.unet(res, int(meta["width"]), int(meta["depth"]))

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
    conv.representative_dataset = lambda: representative(res, a.data, a.samples)
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
