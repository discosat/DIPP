#!/usr/bin/env python3
"""Classification quality per rung, in the deployment domain, with the fixes on.

    analysis/cls_quality_device.py

EuroSAT holdout accuracy (75.67 / 89.98 / 98.17) is measured on EuroSAT's own
processed JPEGs and does not survive the trip to hardware: the same models score
27-31% coarse agreement on expert-verified Sentinel-2 until the rendering is
corrected. This scores all three rungs the way the board now runs them --
EuroSAT's uniform 0.093 gain, no offset, and the tile presented at the 64px
resolution the models were trained at.

Reported per rung so the LADDER can be judged, not just the average: a rung
earns its place only if it is separated from its neighbours in quality as well
as cost.
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
EUROSAT_GAIN = 0.0932          # measured: EuroSAT RGB = reflectance x 0.093
TRAIN_RES = 64                 # EuroSAT native resolution


def main():
    from cs12_classifier_validate import WC_GROUP, EUROSAT, ES_GROUP, GROUPS
    from ai_edge_litert.interpreter import Interpreter
    import cv2

    tiles = []
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
        if float((cl > 0).mean()) > 0.10:      # ground must be visible
            continue
        g = collections.Counter(WC_GROUP.get(int(v)) for v in lc.ravel())
        g.pop(None, None)
        if not g:
            continue
        top, n = g.most_common(1)[0]
        if n / lc.size < 0.6:
            continue
        tiles.append((np.clip(rgb.astype(np.float32) * EUROSAT_GAIN, 0, 255)
                      .astype(np.uint8), top))
    mix = collections.Counter(t for _, t in tiles)
    print(f"{len(tiles)} clear, group-dominant tiles  mix={dict(mix)}\n")

    rungs = [("low  custom_conv", os.path.join(os.path.dirname(HERE),
              "..", "tf-dipp-module", "custom_conv_model_landcover.tflite"), 75.67),
             ("med  qat_a050r224", os.path.join(HERE, "..", "training", "out",
              "qat_a050r224.tflite"), 89.98),
             ("high qat_a140r224", os.path.join(HERE, "..", "training", "out",
              "qat_a140r224.tflite"), 98.17)]
    out = []
    print(f"{'rung':20} {'EuroSAT':>8} {'device':>8}  per-group recall")
    for name, path, euro in rungs:
        if not os.path.exists(path):
            print(f"{name:20} MISSING {path}"); continue
        it = Interpreter(model_path=path); it.allocate_tensors()
        di, do = it.get_input_details()[0], it.get_output_details()[0]
        res = int(di["shape"][1]); so, zo = do["quantization"]
        conf = collections.Counter()
        for t, truth in tiles:
            x = cv2.resize(cv2.resize(t, (TRAIN_RES, TRAIN_RES),
                                      interpolation=cv2.INTER_AREA),
                           (res, res), interpolation=cv2.INTER_LINEAR)
            it.set_tensor(di["index"], x.astype(np.uint8)[None]); it.invoke()
            sc = (it.get_tensor(do["index"]).astype(np.float32) - zo) * so
            conf[(truth, ES_GROUP.get(int(np.argmax(sc)), "?"))] += 1
        tot = sum(conf.values())
        corr = sum(v for (a, b), v in conf.items() if a == b)
        rec = {}
        for g in GROUPS:
            n = sum(v for (a, _), v in conf.items() if a == g)
            rec[g] = (100 * conf.get((g, g), 0) / n) if n else float("nan")
        print(f"{name:20} {euro:7.2f}% {100*corr/tot:7.1f}%  " +
              "  ".join(f"{g}={rec[g]:.0f}%" for g in GROUPS if rec[g] == rec[g]))
        out.append(dict(rung=name, eurosat=euro, device=corr / tot, recall=rec,
                        n=tot))
    json.dump(out, open(os.path.join(HERE, "stage4.5-models",
                                     "cls_quality_device.json"), "w"), indent=1)
    if len(out) == 3:
        d = [r["device"] for r in out]
        print(f"\nladder separation on device: "
              f"{100*(d[1]-d[0]):+.1f} pp then {100*(d[2]-d[1]):+.1f} pp "
              f"(EuroSAT: +14.3 then +8.2)")


if __name__ == "__main__":
    main()
