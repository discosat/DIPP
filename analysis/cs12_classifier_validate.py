#!/usr/bin/env python3
"""Can the land-cover classifiers be trusted for pipeline evaluation?

    analysis/cs12_classifier_validate.py

The pipeline's mission value is computed from classifier decisions: which tiles
are kept, and therefore what is downlinked. That has never been checked against
ground truth on the payload's own sensor -- EuroSAT accuracy is measured on
EuroSAT, and the plan already records the symptom that matters, that class 9
(SeaLake) never appears in a coastal Copenhagen scene.

CloudSEN12's test split now carries ESA WorldCover land cover on the same
Sentinel-2 pixels as the imagery, so the decision is checkable in the
deployment domain.

WHAT THIS CAN AND CANNOT SAY.
  * WorldCover is a 10 m MODEL PRODUCT, not expert labels. It is adequate for
    "is this tile built-up or water" and not a quality benchmark. The cloud
    masks in the same corpus ARE expert; these two must not be quoted with
    equal authority.
  * Its 11 classes do not map onto EuroSAT's 10. There is no WorldCover class
    for Highway or Industrial separately, and none for River against SeaLake.
    So this scores COARSE GROUPS, and a "correct" answer means the prediction
    fell in the right group, not that it matched a label.

Tiles are scored only where one group dominates (default 60% of pixels), because
a tile that is half water and half city has no single right answer and would
just add noise in whichever direction the threshold happened to fall.
"""

import argparse
import collections
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.expanduser("~/dipp-datasets/cloudsen12")

# EuroSAT, alphabetical from image_dataset_from_directory.
EUROSAT = ["AnnualCrop", "Forest", "HerbaceousVegetation", "Highway",
           "Industrial", "Pasture", "PermanentCrop", "Residential", "River",
           "SeaLake"]
# Coarse groups both label sets can express.
ES_GROUP = {0: "crop", 6: "crop", 1: "tree", 2: "herb", 5: "herb",
            3: "built", 4: "built", 7: "built", 8: "water", 9: "water"}
WC_GROUP = {10: "tree", 20: "herb", 30: "herb", 40: "crop", 50: "built",
            80: "water", 90: "herb", 95: "tree", 100: "herb"}
# 60 bare/sparse and 70 snow/ice have no EuroSAT equivalent; tiles dominated by
# them are excluded rather than forced into a group.
GROUPS = ["crop", "tree", "herb", "built", "water"]

# The frozen pipeline rendering (flight-config/module_1.yaml): uniform gain,
# zero offset, clip at 255. Phase 0 result; overridable for A/B only.
GAIN = 0.050
TRAIN_RES = 64      # module_3/4/5.yaml train_res: tiles are 64->224 blurred


def scenes(limit=None, crop=224, dominance=0.6, max_cloud=0.10, split="validation",
           gain=GAIN, crops="grid"):
    """Device-domain tiles with a dominant WorldCover group AND little cloud.

    ⚠️ The cloud filter is not optional, and leaving it out produced a
    meaningless first result. CloudSEN12 is a CLOUD dataset -- 36% of pixels are
    cloud -- so most test tiles are partly or wholly obscured, while WorldCover
    describes the GROUND underneath. Scoring an unfiltered sample asks a
    land-cover classifier to name terrain it cannot see and then marks it wrong
    against the hidden label. Rendering the disagreements showed it immediately:
    the "false positive water" tiles were uniform white cloud labelled
    herbaceous.

    The expert cloud masks in the same corpus make the filter exact, so this
    uses them rather than a brightness heuristic.
    """
    fs = sorted(glob.glob(os.path.join(DATA, split + "_lc", "*.npz")))
    if limit:
        fs = fs[:limit]
    # Several crops per scene rather than one centre crop: the first pass had
    # 202 tiles with built n=6, too few to say anything about the target
    # class. A 2x2 grid of non-overlapping 224 windows in the 509 scene.
    if crops == "grid":
        offs = [(y, x) for y in (0, 509 - crop) for x in (0, 509 - crop)]
    else:
        offs = [((509 - crop) // 2, (509 - crop) // 2)]
    for f in fs:
        sid = os.path.basename(f)[:-4]
        img = os.path.join(DATA, split, sid + ".npz")
        if not os.path.exists(img):
            continue
        with np.load(f) as z:
            lc_full = z["lc"]
        with np.load(img) as z:
            rgb_full = np.transpose(z["rgb"], (1, 2, 0))
            cloud_full = z["label"]
        for y0, x0 in offs:
            lc = lc_full[y0:y0 + crop, x0:x0 + crop]
            rgb = rgb_full[y0:y0 + crop, x0:x0 + crop]
            cl = cloud_full[y0:y0 + crop, x0:x0 + crop]
            # thick(1), thin(2) and shadow(3) all obscure or alter the ground.
            if float((cl > 0).mean()) > max_cloud:
                continue
            grp = collections.Counter(WC_GROUP.get(int(v)) for v in lc.ravel())
            grp.pop(None, None)
            if not grp:
                continue
            top, n = grp.most_common(1)[0]
            if n / lc.size < dominance:
                continue
            tile = np.clip(rgb.astype(np.float32) * gain, 0, 255).astype(np.uint8)
            yield f"{sid}@{y0},{x0}", tile, top


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="scenes (0 = all)")
    ap.add_argument("--dominance", type=float, default=0.6)
    ap.add_argument("--max-cloud", type=float, default=0.10,
                    help="skip tiles more than this fraction cloud/shadow "
                         "by the EXPERT mask; the ground must be visible")
    ap.add_argument("--split", default="validation", choices=["validation", "test"],
                    help="select on validation; quote test once at the end")
    ap.add_argument("--gain", type=float, default=GAIN,
                    help="pipeline rendering gain (frozen at 0.050)")
    ap.add_argument("--crops", default="grid", choices=["grid", "centre"])
    ap.add_argument("--no-train-res", action="store_true",
                    help="skip the module's 64px down/up-sample (train_res: 0)")
    ap.add_argument("--train-res", type=int, default=TRAIN_RES,
                    help="the module's train_res (64 = EuroSAT native; 56 = "
                         "integer 4x scale, ~2x faster INTER_AREA)")
    ap.add_argument("models", nargs="*", help="tflite files (default: the ms_* rungs)")
    ap.add_argument("-o", "--out", default=os.path.join(
        HERE, "stage4.5-models", "classifier_validation.json"))
    a = ap.parse_args()
    from ai_edge_litert.interpreter import Interpreter
    import cv2

    if a.models:
        models = [(os.path.basename(m), m) for m in a.models]
    else:
        o = os.path.join(HERE, "..", "training", "out")
        models = [("ms_low_qat (slot 3)", os.path.join(o, "landcover_ms_low_qat.tflite")),
                  ("ms_medop_qat (slot 4)", os.path.join(o, "landcover_ms_medop_qat.tflite")),
                  ("ms_high_qat (slot 5)", os.path.join(o, "landcover_ms_high_qat.tflite"))]

    tiles = list(scenes(a.limit, dominance=a.dominance, max_cloud=a.max_cloud,
                        split=a.split, gain=a.gain, crops=a.crops))
    mix = collections.Counter(g for _, _, g in tiles)
    print(f"{len(tiles)} tiles from {a.limit or 'all'} {a.split} scenes "
          f"({a.crops} crops, gain {a.gain}): dominant group "
          f">={100*a.dominance:.0f}% of pixels AND <={100*a.max_cloud:.0f}% "
          f"cloud by expert mask")
    print("  group mix:", dict(mix), "\n")
    if not tiles:
        sys.exit("no tiles qualified; lower --dominance or fetch more land cover")

    out = []
    for name, path in models:
        if not os.path.exists(path):
            print(f"{name}: MISSING {path}"); continue
        it = Interpreter(model_path=path); it.allocate_tensors()
        di, do = it.get_input_details()[0], it.get_output_details()[0]
        res = int(di["shape"][1])
        si, zi = di["quantization"]
        if not (abs(si - 2 / 255) < 1e-6 and zi in (127, 128)):
            print(f"{name}: input q=({si:.6f},{zi}) violates the (2/255,128) "
                  f"contract -- raw passthrough would feed the wrong domain; skipped")
            continue
        so, zo = do["quantization"]
        conf = collections.Counter()
        preds = collections.Counter()
        for _, tile, truth in tiles:
            # Exactly the module's chain (tflite_classification.cpp): INTER_AREA
            # down to train_res, INTER_LINEAR up to the tensor -- the tile then
            # carries EuroSAT's 64 px blur; and raw uint8 through.
            x = tile
            if not a.no_train_res and a.train_res < tile.shape[0]:
                x = cv2.resize(x, (a.train_res, a.train_res), interpolation=cv2.INTER_AREA)
                x = cv2.resize(x, (res, res), interpolation=cv2.INTER_LINEAR)
            elif res != x.shape[0]:
                x = cv2.resize(x, (res, res), interpolation=cv2.INTER_AREA)
            it.set_tensor(di["index"], x.astype(np.uint8)[None]); it.invoke()
            sc = (it.get_tensor(do["index"]).astype(np.float32) - zo) * so
            cls = int(np.argmax(sc))
            preds[EUROSAT[cls]] += 1
            conf[(truth, ES_GROUP.get(cls, "?"))] += 1
        tot = sum(conf.values())
        correct = sum(v for (t, p), v in conf.items() if t == p)
        print(f"=== {name} ===")
        print(f"  coarse-group agreement: {100*correct/tot:5.1f}%  ({correct}/{tot})")
        # Precision as well as recall, per group. For a pipeline that RETAINS
        # one class, recall is how much of the target it finds and precision is
        # how much downlink it wastes on everything else -- and with a target
        # class that is 21% of tiles, a model that predicts it half the time can
        # have good recall and still be mostly wrong.
        for g in GROUPS:
            n = sum(v for (t, _), v in conf.items() if t == g)
            pn = sum(v for (_, pp), v in conf.items() if pp == g)
            if not n and not pn:
                continue
            hit = conf.get((g, g), 0)
            rec = 100 * hit / n if n else float("nan")
            prec = 100 * hit / pn if pn else float("nan")
            print(f"    {g:6} n={n:4} predicted={pn:4}  "
                  f"recall {rec:5.1f}%  precision {prec:5.1f}%")
        print(f"  predicted classes: "
              f"{dict(preds.most_common(5))}")
        never = [c for c in EUROSAT if c not in preds]
        print(f"  never predicted: {never or 'none'}\n")
        out.append(dict(model=name, split=a.split, gain=a.gain, crops=a.crops,
                        train_res=0 if a.no_train_res else a.train_res,
                        agreement=correct / tot, n=tot,
                        preds=dict(preds),
                        per_group={g: dict(n=sum(v for (t, _), v in conf.items()
                                                 if t == g),
                                           hit=conf.get((g, g), 0))
                                   for g in GROUPS}))
    json.dump(out, open(a.out, "w"), indent=1)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
