#!/usr/bin/env python3
"""Score a classifier on hand-labelled tiles from real device imagery.

    analysis/device_labelled_eval.py [--model ...] [--models-dir ...]

EuroSAT accuracy says whether a model works on EuroSAT. This says whether it
works on what the satellite actually produces, which is the only number that
justifies anything the paper claims. Labels are in
`stage4.5-models/device_labels.json`: only unambiguous tiles -- open water with
no coastline, dense urban with no fields -- so a model that fails here is not
failing on a hard case.
"""
import argparse, json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from preprocess_replica import device_frame, tiles, CLASSES

DATA = os.path.expanduser("~/dipp2-experiments/sentinel2-dataset")


def evaluate(model_path, labels, data_dir, prep=None):
    from ai_edge_litert.interpreter import Interpreter
    it = Interpreter(model_path=model_path); it.allocate_tensors()
    di, do = it.get_input_details()[0], it.get_output_details()[0]
    size = int(di["shape"][1])
    per_class, preds = {}, {}
    for scene, byclass in labels.items():
        ts = list(tiles(device_frame(os.path.join(data_dir, f"{scene}.bayerRG"))))
        for cname, idxs in byclass.items():
            want = CLASSES.index(cname)
            for i in idxs:
                a = ts[i]
                if prep is not None:
                    a = prep(a)
                if a.shape[0] != size:
                    import cv2
                    a = cv2.resize(a, (size, size), interpolation=cv2.INTER_AREA)
                it.set_tensor(di["index"], np.ascontiguousarray(a)[None, ...])
                it.invoke()
                p = int(np.argmax(it.get_tensor(do["index"])[0]))
                per_class.setdefault(cname, []).append(p == want)
                preds.setdefault(cname, []).append(p)
    return per_class, preds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default="../tf-dipp-module")
    ap.add_argument("--models", nargs="*",
                    default=["custom_conv_model_landcover", "mobilenetv2_05_landcover",
                             "mobilenetv2_14_landcover"])
    ap.add_argument("--data", default=DATA)
    a = ap.parse_args()
    spec = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "stage4.5-models", "device_labels.json")))
    labels = spec["labels"]
    n = sum(len(v) for s in labels.values() for v in s.values())
    print(f"{n} hand-labelled tiles from {len(labels)} real scenes\n")
    print(f"{'model':32} {'overall':>8} {'SeaLake':>9} {'Residential':>12}   most-predicted")
    print("-" * 92)
    for m in a.models:
        p = os.path.join(a.models_dir, f"{m}.tflite")
        if not os.path.exists(p):
            continue
        pc, pr = evaluate(p, labels, a.data)
        allv = [x for v in pc.values() for x in v]
        import collections
        top = collections.Counter(x for v in pr.values() for x in v).most_common(2)
        top = " ".join(f"{CLASSES[c]}={k}" for c, k in top)
        print(f"{m:32} {np.mean(allv):7.1%} {np.mean(pc['SeaLake']):8.1%} "
              f"{np.mean(pc['Residential']):11.1%}   {top}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
