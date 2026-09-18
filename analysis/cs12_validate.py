#!/usr/bin/env python3
"""Score exported segmentation rungs against EXPERT cloud masks.

    analysis/cs12_validate.py --models analysis/stage4.5-models/cs12_rungs

Replaces a proxy with a measurement. Rungs have so far been ranked by masked
fraction on device frames believed to be cloud-free -- which cannot tell
over-prediction from correctly finding faint haze, and that distinction is
exactly what decides the ladder: the best-separating rung masks 19.86% of
`koge-coast-no-clouds`, and whether that is a defect or a virtue is currently an
assumption.

CloudSEN12's test split has expert-reviewed per-pixel labels, so precision and
recall are computable. Two properties make it the right corpus:

  * Sentinel-2 L1C, the payload's sensor and processing level.
  * Scored through the SAME device affine the training used, so the numbers
    describe the radiometric domain the hardware presents.

Scored the way the MODULE behaves, not the way a paper would flatter it: the
tile is resized with INTER_AREA, fed as raw uint8, and thresholded at
pixel_threshold. A float path would report better numbers than ship.

FALSE POSITIVES ARE REPORTED SEPARATELY on clear-only scenes, because they are
the asymmetric error here: a false negative leaves cloud in a downlinked tile,
while a false positive discards ground that cannot be recovered. IoU weighs
those equally; the mission does not.
"""

import argparse
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = os.path.expanduser("~/dipp-datasets/cloudsen12")
CLEAR, THICK, THIN, SHADOW = 0, 1, 2, 3


AFFINE = {"mode": "device"}   # set from --affine before scoring


def device_domain(rgb16):
    """Reflectance -> uint8, in the rendering the MODEL WAS TRAINED THROUGH.

    This used to hardcode the old device affine. That silently mis-renders any
    model trained at a different gain -- and Phase 0 trains models at gains
    0.050 and 0.060 with zero offset. The affine is a property of the model, so
    it is passed in, and clipping follows it: the 182/165/164 ceiling belongs to
    the old affine only; a uniform gain clips at 255.
    """
    m = AFFINE["mode"]
    x = rgb16.astype(np.float32)
    if m == "device":
        gain = np.array([0.040321, 0.031413, 0.025168], np.float32)
        off = np.array([17.270, 36.860, 60.976], np.float32)
        ceil = np.array([182.0, 165.0, 164.0], np.float32)
        return np.clip(x * gain + off, 0, ceil).astype(np.uint8)
    if m.startswith("gain:"):
        g = float(m.split(":", 1)[1])
        return np.clip(x * g, 0, 255).astype(np.uint8)
    raise SystemExit(f"unknown affine {m!r}")


SPLIT = {"name": "validation"}   # select on validation; report on test once


def load_test(limit=None, valid=509):
    """Scenes as (uint8 rendered rgb, label), padding trimmed."""
    fs = sorted(glob.glob(os.path.join(DATA, SPLIT["name"], "*.npz")))
    if not fs:
        sys.exit(f"no samples under {DATA}/{SPLIT['name']}")
    if limit:
        fs = fs[:limit]
    for f in fs:
        with np.load(f) as z:
            rgb = np.transpose(z["rgb"], (1, 2, 0))[:valid, :valid]
            lab = z["label"][:valid, :valid]
        yield os.path.basename(f)[:-4], device_domain(rgb), lab


def score(model, thin_is_cloud=True, shadow_is_cloud=False, limit=None,
          threshold=0.5, crop=None):
    import cv2
    if model.endswith(".weights.npz"):
        # FLOAT graph, rebuilt from the training builder: separates
        # quantisation loss from the metric itself (training-time val IoU is
        # on crops; this scores device-footprint tiles of whole scenes).
        import json as _json, tensorflow as tf
        sys.path.insert(0, os.path.join(HERE, "..", "training"))
        import train_cloud as tc
        meta = _json.load(open(model.replace(".weights.npz", ".json")))
        res = int(meta["res"])
        if meta.get("backbone") == "mobilenetv2":
            m = tc.unet_mobilenet(res, float(meta["alpha"]), int(meta.get("dec_max", 128)))
        else:
            m = tc.unet(res, int(meta["width"]), int(meta["depth"]))
        z = np.load(model)
        m.set_weights([z[f"arr_{i}"] for i in range(len(z.files))])
        def predict(x):
            return m.predict((x.astype(np.float32) / 127.5 - 1.0)[None], verbose=0)[0, :, :, 0]
    else:
        try:
            from ai_edge_litert.interpreter import Interpreter
        except ImportError:
            import tensorflow as tf
            Interpreter = tf.lite.Interpreter
        it = Interpreter(model_path=model)
        it.allocate_tensors()
        di, do = it.get_input_details()[0], it.get_output_details()[0]
        res = int(di["shape"][1])
        so, zo = do["quantization"]
        def predict(x):
            it.set_tensor(di["index"], x.astype(np.uint8)[None])
            it.invoke()
            return (it.get_tensor(do["index"]).astype(np.float32) - zo)[0, :, :, 0] * so

    tp = fp = fn = tn = 0
    clear_px = clear_fp = 0
    per_scene = []          # (tp, fp, fn) per scene, for bootstrapping differences
    for _, rgb, lab in load_test(limit):
        gt = (lab == THICK)
        if thin_is_cloud:
            gt |= (lab == THIN)
        if shadow_is_cloud:
            gt |= (lab == SHADOW)
        # Centre crop to a device-tile footprint, then resize as the module
        # does. A device tile is a 224px crop of a frame, so scoring a whole
        # 509px scene would test a ground-sample distance the payload never
        # sees.
        c = crop or 224
        y0 = (rgb.shape[0] - c) // 2
        x0 = (rgb.shape[1] - c) // 2
        t = rgb[y0:y0 + c, x0:x0 + c]
        g = gt[y0:y0 + c, x0:x0 + c]
        x = t if t.shape[0] == res else cv2.resize(
            t, (res, res), interpolation=cv2.INTER_AREA)
        pred = predict(x) > threshold
        if pred.shape != g.shape:
            pred = cv2.resize(pred.astype(np.uint8), (g.shape[1], g.shape[0]),
                              interpolation=cv2.INTER_NEAREST).astype(bool)
        stp, sfp, sfn = int((pred & g).sum()), int((pred & ~g).sum()), int((~pred & g).sum())
        per_scene.append((stp, sfp, sfn))
        tp += stp; fp += sfp; fn += sfn; tn += int((~pred & ~g).sum())
        # Scenes with no cloud at all: every positive here is a false alarm,
        # and false alarms destroy ground irrecoverably.
        if not g.any():
            clear_px += g.size
            clear_fp += int(pred.sum())

    iou = tp / (tp + fp + fn) if (tp + fp + fn) else float("nan")
    prec = tp / (tp + fp) if (tp + fp) else float("nan")
    rec = tp / (tp + fn) if (tp + fn) else float("nan")
    return dict(iou=iou, precision=prec, recall=rec,
                fp_rate_on_clear=(clear_fp / clear_px) if clear_px else float("nan"),
                clear_scenes_px=clear_px, tp=tp, fp=fp, fn=fn, tn=tn,
                per_scene=per_scene)


def bootstrap_diff(a, b, n=2000, seed=43):
    """95% CI of IoU(a) - IoU(b), resampling SCENES (paired)."""
    A, B = np.array(a["per_scene"]), np.array(b["per_scene"])
    rng = np.random.default_rng(seed); m = len(A); d = []
    for _ in range(n):
        idx = rng.integers(0, m, m)
        sa, sb = A[idx].sum(0), B[idx].sum(0)
        d.append(sa[0] / sa.sum() - sb[0] / sb.sum())
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=os.path.join(HERE, "stage4.5-models",
                                                     "cs12_rungs_g050"))   # the deployed (gain 0.050) rungs
    ap.add_argument("--limit", type=int, help="scenes (default all)")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--thin-clear", action="store_true")
    # default is the FROZEN rendering (module_1.yaml, gain 0.050, zero offset):
    # 2026-09-15 a test-split pass under the old 'device' default scored the
    # deployed rungs at IoU 0.41-0.64 -- mis-rendered inputs, not a result.
    ap.add_argument("--affine", default="gain:0.050",
                    help="rendering the models were TRAINED through: 'device' "
                         "(old affine) or 'gain:<G>' (uniform, zero offset)")
    ap.add_argument("--split", choices=["validation", "test"], default="validation",
                    help="select rungs on validation; quote test once at the end")
    ap.add_argument("-o", "--out", default=os.path.join(
        HERE, "stage4.5-models", "cs12_validation.json"))
    a = ap.parse_args()

    AFFINE["mode"] = a.affine
    SPLIT["name"] = a.split
    # --models may be a directory of ptq_*.tflite, or a glob of .weights.npz
    # (float graphs, scored through the same loop -- see score()).
    models = (sorted(glob.glob(os.path.join(a.models, "ptq_*.tflite")))
              if os.path.isdir(a.models) else sorted(glob.glob(a.models)))
    if not models:
        sys.exit(f"no ptq_*.tflite under {a.models}")
    n = len(list(glob.glob(os.path.join(DATA, a.split, "*.npz"))))
    print(f"{len(models)} models against {a.limit or n} expert-labelled "
          f"{a.split} scenes, affine={a.affine}, "
          f"thin={'clear' if a.thin_clear else 'cloud'}, "
          f"threshold={a.threshold}\n")
    print(f"{'rung':26} {'IoU':>7} {'prec':>7} {'recall':>7} {'FP on clear':>12}")
    rows = []
    for m in models:
        tag = (os.path.basename(m)[4:-7] if m.endswith(".tflite")
               else "float_" + os.path.basename(m).replace("cloud_", "").replace(".weights.npz", ""))
        r = score(m, thin_is_cloud=not a.thin_clear, limit=a.limit,
                  threshold=a.threshold)
        r["tag"] = tag
        rows.append(r)
        print(f"{tag:26} {r['iou']:7.4f} {r['precision']:7.4f} "
              f"{r['recall']:7.4f} {100*r['fp_rate_on_clear']:11.2f}%", flush=True)
    # Adjacent-rung differences with a paired scene bootstrap: a ladder step
    # is only a step if its CI excludes zero.
    rows.sort(key=lambda r: r["iou"])
    for lo, hi in zip(rows, rows[1:]):
        c = bootstrap_diff(hi, lo)
        print(f"  {hi['tag']} - {lo['tag']}: {hi['iou']-lo['iou']:+.4f}  95% CI [{c[0]:+.4f}, {c[1]:+.4f}]"
              f"{'' if c[0] > 0 else '   NOT above noise'}")
    json.dump(rows, open(a.out, "w"), indent=1)
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
