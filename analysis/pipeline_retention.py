#!/usr/bin/env python3
"""Retention through the pipeline on CloudSEN12, per effort combination.

    analysis/pipeline_retention.py [--split validation] [--limit N]

Every downstream cost in the paper depends on how many tiles each stage
passes on, and that has only ever been assumed. This runs the classification
and segmentation rungs exactly as the modules do (frozen rendering, the
224->56->224 resize for classification, raw uint8 passthrough, argmax ==
class_index 9 = SeaLake to keep, then clear pixels > 50% to keep) over every
224 px crop of every scene in the split -- clouds included, because the
pipeline sees whatever the sensor sends -- and reports:

  * classification retention  P(kept | all tiles), per rung
  * segmentation retention    P(kept | classification kept), per rung
  * end-to-end retention      what reaches the encoder, per (cls, seg) pair
  * VALUE of what is retained: of the tiles reaching the encoder, the share
    that is genuinely water-dominated (WorldCover >= 60%) and genuinely
    cloud-free (expert mask <= 10%) -- the mission outcome the reviewers asked
    to see quantified, per effort combination.

Labels: WorldCover is a model product (adequate for "is this water"); the
cloud masks are expert.
"""
import argparse, collections, glob, itertools, json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = os.path.expanduser("~/dipp-datasets/cloudsen12")
GAIN, TRAIN_RES, CROP = 0.050, 56, 224
WATER = 9
CLS = {"low": "landcover_ms_low_qat", "med": "landcover_ms_medop_qat", "high": "landcover_ms_high_qat"}
SEG = {"low": "ptq_cs12g050_p_w08d3_r096", "med": "ptq_cs12g050q_g_a035_r160", "high": "ptq_cs12g050q_g_a14_r224"}


def tiles(split, limit):
    from cs12_classifier_validate import WC_GROUP
    fs = sorted(glob.glob(os.path.join(DATA, split, "*.npz")))
    if limit:
        fs = fs[:limit]
    offs = [(y, x) for y in (0, 509 - CROP) for x in (0, 509 - CROP)]
    for f in fs:
        sid = os.path.basename(f)[:-4]
        lcf = os.path.join(DATA, split + "_lc", sid + ".npz")
        with np.load(f) as z:
            rgb = np.transpose(z["rgb"], (1, 2, 0)); lab = z["label"]
        lc = np.load(lcf)["lc"] if os.path.exists(lcf) else None
        for y0, x0 in offs:
            t = np.clip(rgb[y0:y0+CROP, x0:x0+CROP].astype(np.float32) * GAIN, 0, 255).astype(np.uint8)
            cl = lab[y0:y0+CROP, x0:x0+CROP]
            cloud = float(((cl == 1) | (cl == 2)).mean())
            water = None
            if lc is not None:
                g = lc[y0:y0+CROP, x0:x0+CROP]
                water = float(np.mean([WC_GROUP.get(int(v)) == "water" for v in g.ravel()]))
            yield np.ascontiguousarray(t), cloud, water


class Decider:
    """The six deployed rungs' keep/discard decisions on one rendered tile,
    exactly as the modules take them: classification on the train_res 56
    down/up-scaled tile (argmax == water), segmentation on the model's own
    resolution (kept if the clear fraction > 0.5)."""

    def __init__(self):
        from ai_edge_litert.interpreter import Interpreter
        O = os.path.join(HERE, "..", "training", "out"); R = os.path.join(HERE, "stage4.5-models", "cs12_rungs_g050")
        self.cls = {k: Interpreter(model_path=os.path.join(O, v + ".tflite")) for k, v in CLS.items()}
        self.seg = {k: Interpreter(model_path=os.path.join(R, v + ".tflite")) for k, v in SEG.items()}
        for it in list(self.cls.values()) + list(self.seg.values()):
            it.allocate_tensors()

    def __call__(self, t):
        import cv2
        r = dict(cls={}, seg={})
        x56 = cv2.resize(cv2.resize(t, (TRAIN_RES, TRAIN_RES), interpolation=cv2.INTER_AREA),
                         (224, 224), interpolation=cv2.INTER_LINEAR)
        for k, it in self.cls.items():
            di, do = it.get_input_details()[0], it.get_output_details()[0]
            it.set_tensor(di["index"], x56[None]); it.invoke()
            r["cls"][k] = int(np.argmax(it.get_tensor(do["index"])[0])) == WATER
        for k, it in self.seg.items():
            di, do = it.get_input_details()[0], it.get_output_details()[0]
            res = int(di["shape"][1]); so, zo = do["quantization"]
            x = t if res == CROP else cv2.resize(t, (res, res), interpolation=cv2.INTER_AREA)
            it.set_tensor(di["index"], x[None]); it.invoke()
            sc = (it.get_tensor(do["index"]).astype(np.float32)[0, :, :, 0] - zo) * so
            clear = float((sc < 0.5).mean())          # class 0 = clear kept
            r["seg"][k] = clear > 0.5
        return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="validation")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "stage4.5-models", "pipeline_retention.json"))
    a = ap.parse_args()
    decide = Decider()

    rows = []
    for t, cloud, water in tiles(a.split, a.limit):
        r = dict(cloud=cloud, water=water, **decide(t))
        rows.append(r)
    n = len(rows)
    print(f"{n} tiles from {a.split} ({a.limit or 'all'} scenes x 4 crops); "
          f"truly water&clear: {100*np.mean([bool(r['water'] and r['water']>=0.6 and r['cloud']<=0.1) for r in rows]):.1f}%\n")
    out = dict(n=n, split=a.split, cls={}, seg={}, e2e={})
    print("classification retention (argmax == SeaLake):")
    for k in CLS:
        p = np.mean([r["cls"][k] for r in rows]); out["cls"][k] = float(p)
        print(f"  {k:5} {100*p:5.1f}%")
    print("\nsegmentation retention on classification-kept tiles (clear > 50%):")
    for ck, sk in itertools.product(CLS, SEG):
        kept = [r for r in rows if r["cls"][ck]]
        p = np.mean([r["seg"][sk] for r in kept]) if kept else float("nan")
        e2e = [r for r in kept if r["seg"][sk]]
        val = np.mean([bool(r["water"] and r["water"] >= 0.6 and r["cloud"] <= 0.1) for r in e2e]) if e2e else float("nan")
        cloudy = np.mean([r["cloud"] > 0.5 for r in e2e]) if e2e else float("nan")
        out["e2e"][f"{ck}/{sk}"] = dict(seg_ret=float(p), e2e_ret=len(e2e) / n, value=float(val), n=len(e2e),
                                        majority_cloud=float(cloudy))
        print(f"  cls {ck:5} seg {sk:5}: seg keeps {100*p:5.1f}%  -> e2e {100*len(e2e)/n:5.1f}% of tiles reach the encoder"
              f"  | water&clear share {100*val:5.1f}%  majority-cloud share {100*cloudy:4.1f}%  (n={len(e2e)})")
    # Where the retained tiles actually fall -- the answer to "why is the low
    # rung's downlink so much cleaner": none of the classifiers has seen a
    # cloud, so cloudy tiles land in SOME class, and the strict value criterion
    # (>=60% water AND <=10% cloud) counts mixed-coast tiles as misses.
    def cat(r):
        w = r["water"] or 0.0
        if r["cloud"] > 0.5: return "majority cloud"
        if r["cloud"] > 0.1: return "partly cloudy (10-50%)"
        if w >= 0.6: return "water & clear"
        if w >= 0.2: return "mixed coast (20-60% water)"
        return "not water, clear"
    cats = ["water & clear", "mixed coast (20-60% water)", "partly cloudy (10-50%)", "majority cloud", "not water, clear"]
    print("\nwhere the classification-retained tiles fall (share of retained, and count):")
    for k in CLS:
        kept = [r for r in rows if r["cls"][k]]
        c = collections.Counter(cat(r) for r in kept)
        print(f"  {k:5} n={len(kept):4}: " + "  ".join(f"{x}: {100*c[x]/max(len(kept),1):4.0f}% ({c[x]})" for x in cats))
    print("after the matching segmentation rung (what is uploaded):")
    for k in CLS:
        kept = [r for r in rows if r["cls"][k] and r["seg"][k]]
        c = collections.Counter(cat(r) for r in kept)
        print(f"  {k:5} n={len(kept):4}: " + "  ".join(f"{x}: {100*c[x]/max(len(kept),1):4.0f}% ({c[x]})" for x in cats))
    out["breakdown"] = {k: dict(collections.Counter(cat(r) for r in rows if r["cls"][k])) for k in CLS}
    json.dump(out, open(a.out, "w"), indent=1)
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    sys.exit(main())
