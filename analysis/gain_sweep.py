#!/usr/bin/env python3
"""Choose ONE pipeline rendering that serves classification and segmentation.

    analysis/gain_sweep.py

There is one demosaic stage, so both stages consume identical tiles, and the
gain is therefore a joint design parameter rather than two independent choices.
The tension is measurable: high gain gives the classifiers the contrast their
EuroSAT training had, and saturates cloud; low gain preserves cloud structure
and pushes vegetation dark enough to be mistaken for water.

Each stage is scored differently, and deliberately so:

  CLASSIFICATION is scored directly -- the models are fixed, their weights do
  not depend on the rendering, so coarse-group agreement at each gain is the
  real number.

  SEGMENTATION cannot be scored directly, because its models WERE trained
  through a particular affine and would have to be retrained per candidate. So
  it is scored by what a retrained model could exploit: how separable clear /
  thin / thick remain after rendering, and how much of each class saturates.
  Saturation is irreversible -- once thick cloud clips at 255 no amount of
  training recovers the structure -- so it is reported as a hard constraint
  rather than folded into a score.
"""

import collections
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = os.path.expanduser("~/dipp-datasets/cloudsen12")
GAINS = [0.040, 0.050, 0.060, 0.070, 0.080, 0.0932]
TRAIN_RES = 64
CLEAR, THICK, THIN = 0, 1, 2


def separability(a, b):
    """Fisher-style class distance: |mean gap| over pooled spread, per band.

    Not an IoU and not pretending to be. It measures whether the rendering
    still carries the difference a model would have to learn -- if clear and
    cloud collapse together in 8-bit, no training recovers it.
    """
    m = np.abs(a.mean(0) - b.mean(0))
    s = np.sqrt(0.5 * (a.var(0) + b.var(0))) + 1e-6
    return float((m / s).mean())


def main():
    from cs12_classifier_validate import WC_GROUP, EUROSAT, ES_GROUP
    from ai_edge_litert.interpreter import Interpreter
    import cv2

    # classification tiles: clear ground, one dominant land-cover group
    cls_tiles = []
    for f in sorted(glob.glob(os.path.join(DATA, "test_lc", "*.npz"))):
        sid = os.path.basename(f)[:-4]
        img = os.path.join(DATA, "test", sid + ".npz")
        if not os.path.exists(img):
            continue
        with np.load(f) as z:
            lc = z["lc"][142:366, 142:366]
        with np.load(img) as z:
            rgb = np.transpose(z["rgb"], (1, 2, 0))[142:366, 142:366]
            cl = z["label"][142:366, 142:366]
        if float((cl > 0).mean()) > 0.10:
            continue
        g = collections.Counter(WC_GROUP.get(int(v)) for v in lc.ravel())
        g.pop(None, None)
        if not g:
            continue
        top, n = g.most_common(1)[0]
        if n / lc.size >= 0.6:
            cls_tiles.append((rgb.astype(np.float32), top))

    # segmentation pixels: class-labelled reflectance, sampled
    px = {CLEAR: [], THIN: [], THICK: []}
    for f in sorted(glob.glob(os.path.join(DATA, "test", "*.npz")))[:150]:
        with np.load(f) as z:
            rgb = np.transpose(z["rgb"], (1, 2, 0))[:509, :509].astype(np.float32)
            lab = z["label"][:509, :509]
        for k in px:
            sel = rgb[lab == k]
            if len(sel):
                px[k].append(sel[::60])
    px = {k: np.concatenate(v) for k, v in px.items() if v}
    print(f"{len(cls_tiles)} classification tiles, "
          f"{{{', '.join(f'{k}:{len(v)}' for k, v in px.items())}}} labelled pixels\n")

    models = [("a050", os.path.join(HERE, "..", "training", "out",
                                    "qat_a050r224.tflite")),
              ("a140", os.path.join(HERE, "..", "training", "out",
                                    "qat_a140r224.tflite"))]
    interps = []
    for nm, p in models:
        it = Interpreter(model_path=p); it.allocate_tensors()
        interps.append((nm, it, it.get_input_details()[0],
                        it.get_output_details()[0]))

    print(f"{'gain':>7} {'cls a050':>9} {'cls a140':>9} "
          f"{'sep clr/thick':>14} {'sep thin/thick':>15} "
          f"{'sat thick':>10} {'sat clear':>10}")
    rows = []
    for G in GAINS:
        accs = {}
        for nm, it, di, do in interps:
            res = int(di["shape"][1]); so, zo = do["quantization"]
            conf = collections.Counter()
            for r, truth in cls_tiles:
                x = np.clip(r * G, 0, 255).astype(np.uint8)
                x = cv2.resize(cv2.resize(x, (TRAIN_RES, TRAIN_RES),
                                          interpolation=cv2.INTER_AREA),
                               (res, res), interpolation=cv2.INTER_LINEAR)
                it.set_tensor(di["index"], x[None]); it.invoke()
                sc = (it.get_tensor(do["index"]).astype(np.float32) - zo) * so
                conf[(truth, ES_GROUP.get(int(np.argmax(sc)), "?"))] += 1
            tot = sum(conf.values())
            accs[nm] = sum(v for (a, b), v in conf.items() if a == b) / tot
        r = {k: np.clip(v * G, 0, 255) for k, v in px.items()}
        s_ct = separability(r[CLEAR], r[THICK])
        s_tt = separability(r[THIN], r[THICK])
        sat_thick = float(np.mean((px[THICK] * G) >= 255))
        sat_clear = float(np.mean((px[CLEAR] * G) >= 255))
        print(f"{G:7.4f} {100*accs['a050']:8.1f}% {100*accs['a140']:8.1f}% "
              f"{s_ct:14.3f} {s_tt:15.3f} "
              f"{100*sat_thick:9.1f}% {100*sat_clear:9.1f}%")
        rows.append(dict(gain=G, cls_a050=accs["a050"], cls_a140=accs["a140"],
                         sep_clear_thick=s_ct, sep_thin_thick=s_tt,
                         sat_thick=sat_thick, sat_clear=sat_clear))
    json.dump(rows, open(os.path.join(HERE, "stage4.5-models",
                                      "gain_sweep.json"), "w"), indent=1)
    print("\nsaturation is a hard constraint, not a tradeoff: clipped cloud "
          "cannot be recovered by retraining")


if __name__ == "__main__":
    main()
