#!/usr/bin/env python3
"""Retention with the stages SWAPPED: segmentation first, then classification.

    analysis/pipeline_retention_swapped.py

Why. In the deployed order the classifiers -- which have never seen a cloud --
retain hundreds of cloud-covered tiles as 'water' (medium: 222 of 419
retained tiles are majority cloud) and segmentation only strips the
majority-cloud ones afterwards. Segmentation first would keep the cloud away
from the classifier. This measures that on the same 2,140 validation tiles
through the same module chain, with one fidelity the swap adds: after
segmentation the classifier sees the tile WITH the module's cloud fill
applied (5x5 Gaussian where the model said cloud, mask_lowpass 5), not the
raw tile. Cost changes too: segmentation now runs on all 99 tiles and
classification only on what survives, so both views (normal and
retention-matched) are reported by analysis/value_of_effort.py.
"""
import collections, itertools, json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pipeline_retention import tiles, CLS, SEG, WATER, TRAIN_RES, CROP


def cat(r):
    w = r["water"] or 0.0
    if r["cloud"] > 0.5: return "majority cloud"
    if r["cloud"] > 0.1: return "partly cloudy (10-50%)"
    if w >= 0.6: return "water & clear"
    if w >= 0.2: return "mixed coast (20-60% water)"
    return "not water, clear"


CATS = ["water & clear", "mixed coast (20-60% water)", "partly cloudy (10-50%)", "majority cloud", "not water, clear"]


def main():
    import cv2
    from ai_edge_litert.interpreter import Interpreter
    O = os.path.join(HERE, "..", "training", "out"); R = os.path.join(HERE, "stage4.5-models", "cs12_rungs_g050")
    cls = {k: Interpreter(model_path=os.path.join(O, v + ".tflite")) for k, v in CLS.items()}
    seg = {k: Interpreter(model_path=os.path.join(R, v + ".tflite")) for k, v in SEG.items()}
    for it in list(cls.values()) + list(seg.values()):
        it.allocate_tensors()

    rows = []
    for t, cloud, water in tiles("validation", 0):
        r = dict(cloud=cloud, water=water, seg={}, cls={})
        blurred = cv2.GaussianBlur(t, (5, 5), 0)
        for sk, it in seg.items():
            di, do = it.get_input_details()[0], it.get_output_details()[0]
            res = int(di["shape"][1]); so, zo = do["quantization"]
            x = t if res == CROP else cv2.resize(t, (res, res), interpolation=cv2.INTER_AREA)
            it.set_tensor(di["index"], x[None]); it.invoke()
            sc = (it.get_tensor(do["index"]).astype(np.float32)[0, :, :, 0] - zo) * so
            mask = sc >= 0.5
            if mask.shape != (CROP, CROP):
                mask = cv2.resize(mask.astype(np.uint8), (CROP, CROP), interpolation=cv2.INTER_NEAREST).astype(bool)
            keep = float((~mask).mean()) > 0.5
            r["seg"][sk] = keep
            if not keep:
                continue
            filled = np.where(mask[..., None], blurred, t)          # the module's fill
            x56 = cv2.resize(cv2.resize(filled, (TRAIN_RES, TRAIN_RES), interpolation=cv2.INTER_AREA),
                             (CROP, CROP), interpolation=cv2.INTER_LINEAR)
            for ck, cit in cls.items():
                cdi, cdo = cit.get_input_details()[0], cit.get_output_details()[0]
                cit.set_tensor(cdi["index"], x56[None]); cit.invoke()
                r["cls"][(sk, ck)] = int(np.argmax(cit.get_tensor(cdo["index"])[0])) == WATER
        rows.append(r)
    n = len(rows)
    out = dict(n=n, order="segmentation -> classification", seg={}, e2e={}, breakdown={})
    print(f"{n} tiles, segmentation FIRST\n\nsegmentation retention (clear > 50%) on all tiles:")
    for sk in SEG:
        p = np.mean([r["seg"][sk] for r in rows]); out["seg"][sk] = float(p)
        print(f"  {sk:5} {100*p:5.1f}%")
    print("\nclassification (argmax == water) on segmentation-kept, filled tiles:")
    for sk, ck in itertools.product(SEG, CLS):
        kept = [r for r in rows if r["seg"][sk]]
        e2e = [r for r in kept if r["cls"].get((sk, ck))]
        val = np.mean([bool(r["water"] and r["water"] >= 0.6 and r["cloud"] <= 0.1) for r in e2e]) if e2e else float("nan")
        c = collections.Counter(cat(r) for r in e2e)
        out["e2e"][f"{sk}/{ck}"] = dict(cls_ret=len(e2e) / max(len(kept), 1), e2e_ret=len(e2e) / n, value=float(val), n=len(e2e),
                                        breakdown=dict(c))
        print(f"  seg {sk:5} cls {ck:5}: cls keeps {100*len(e2e)/max(len(kept),1):5.1f}% of {len(kept)} -> e2e {100*len(e2e)/n:5.1f}%"
              f"  | water&clear {100*val:5.1f}%  (n={len(e2e)})  " +
              "  ".join(f"{x.split(' (')[0]}: {c[x]}" for x in CATS))
    p = os.path.join(HERE, "stage4.5-models", "pipeline_retention_swapped.json")
    json.dump(out, open(p, "w"), indent=1)
    print(f"\nwrote {p}")


if __name__ == "__main__":
    sys.exit(main())
