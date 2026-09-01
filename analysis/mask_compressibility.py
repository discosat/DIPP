#!/usr/bin/env python3
"""Does blacking out clouds actually make a tile compress better?

    analysis/mask_compressibility.py

Section IV-A2 states the segmentation mask is applied "to set the cloud-covered
areas to black pixels to provide better compressibility". That is an assertion
about the encoder, and it is cheap to check, so it should be checked: masking
removes bright textured cloud (good) but introduces a hard mask boundary (bad),
and which wins is not obvious a priori.

Method. Real tiles from the device preprocessing chain -- the same 224x224 the
encoder receives in flight -- masked with a real cloud model's prediction, then
encoded with libjxl at the three effort levels the pipeline uses. The module
sets only JXL_ENC_FRAME_SETTING_EFFORT (distance and lossless are commented out
at jpegxl_module.c:57-61), so this uses libjxl's default distance of 1.0 to
match.
"""

import argparse
import json
import os
import sys

import numpy as np
from ai_edge_litert.interpreter import Interpreter
import imagecodecs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from device_labelled_eval import calibrated_tiles, DATA   # noqa: E402

EFFORTS = [1, 4, 7]

# Matched-quality operating points, so the two codecs are compared where they
# actually produce comparable pictures rather than at nominally similar knobs.
# Masking is not obviously good for a wavelet codec: blacking out a region adds
# a hard edge, and edges are what wavelets spend coefficients on.
CODECS = {
    "jxl d=1.0":  lambda x: imagecodecs.jpegxl_encode(x, effort=4, distance=1.0),
    "jxl d=0.5":  lambda x: imagecodecs.jpegxl_encode(x, effort=4, distance=0.5),
    "jxl lossless": lambda x: imagecodecs.jpegxl_encode(x, effort=7, lossless=True),
    "j2k lvl=50": lambda x: imagecodecs.jpeg2k_encode(x, level=50),
    "j2k lvl=40": lambda x: imagecodecs.jpeg2k_encode(x, level=40),
    "j2k reversible": lambda x: imagecodecs.jpeg2k_encode(x, reversible=True),
}


def masks_for(model_path, tiles):
    it = Interpreter(model_path=model_path)
    it.allocate_tensors()
    di, do = it.get_input_details()[0], it.get_output_details()[0]
    size = int(di["shape"][1])
    scale, zp = do["quantization"]
    out = []
    for t in tiles:
        a = t
        if a.shape[0] != size:
            import cv2
            a = cv2.resize(a, (size, size), interpolation=cv2.INTER_AREA)
        it.set_tensor(di["index"], np.ascontiguousarray(a)[None, ...])
        it.invoke()
        raw = it.get_tensor(do["index"])[0].astype(np.float32).reshape(size, size)
        m = ((raw - zp) * scale) >= 0.5          # True = cloud
        if m.shape[0] != t.shape[0]:
            import cv2
            m = cv2.resize(m.astype(np.uint8), (t.shape[1], t.shape[0]),
                           interpolation=cv2.INTER_NEAREST).astype(bool)
        out.append(m)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="training/out/tf29_cloud_cl_high.tflite")
    ap.add_argument("--scenes", nargs="*", default=["amager-light-clouds",
                                                    "koge-coast-light-clouds",
                                                    "koge-coast-full-clouds"])
    ap.add_argument("--max-tiles", type=int, default=40)
    ap.add_argument("-o", "--out", default="analysis/stage4.5-models/mask_compress.json")
    a = ap.parse_args()

    rows = []
    for scene in a.scenes:
        src = os.path.join(DATA, f"{scene}.bayerRG")
        if not os.path.exists(src):
            print(f"  skip {scene}: not found", file=sys.stderr)
            continue
        tiles = calibrated_tiles(src)
        rng = np.random.default_rng(43)
        idx = rng.permutation(len(tiles))[:a.max_tiles]
        sel = [tiles[i] for i in idx]
        ms = masks_for(a.model, sel)
        for t, m in zip(sel, ms):
            frac = float(m.mean())
            masked = t.copy()
            masked[m] = 0
            r = {"scene": scene, "cloud_frac": frac}
            for name, enc in CODECS.items():
                r[f"orig::{name}"] = len(enc(t))
                r[f"mask::{name}"] = len(enc(masked))
            rows.append(r)
        print(f"{scene}: {len(sel)} tiles, mean cloud fraction "
              f"{np.mean([r['cloud_frac'] for r in rows[-len(sel):]]):.1%}")

    if not rows:
        sys.exit("no tiles")

    frac_all = np.array([r["cloud_frac"] for r in rows])
    for label, keep in (("all tiles", np.ones(len(rows), bool)),
                        ("survive low-effort threshold 0.05", frac_all < 0.95),
                        ("survive high-effort threshold 0.70", frac_all < 0.30)):
        if not keep.any():
            continue
        print(f"\n--- {label} (n={keep.sum()}, mean cloud "
              f"{frac_all[keep].mean():.1%}) ---")
        print(f"{'codec':>16}{'orig B':>10}{'masked B':>10}{'change':>9}"
              f"{'tiles smaller':>15}")
        for name in CODECS:
            o = np.array([r[f"orig::{name}"] for r in rows], float)[keep]
            m = np.array([r[f"mask::{name}"] for r in rows], float)[keep]
            print(f"{name:>16}{o.mean():10.0f}{m.mean():10.0f}"
                  f"{(m.mean() / o.mean() - 1) * 100:+8.1f}%"
                  f"{(m < o).mean():14.0%}")

    # The claim is only interesting if the benefit tracks how much is masked.
    frac = frac_all
    print("\nsize change vs how much of the tile is masked:")
    print(f"{'cloud':>12}{'n':>5}" + "".join(f"{k:>16}" for k in CODECS))
    for lo, hi in ((0, .05), (.05, .2), (.2, .5), (.5, 1.01)):
        sel = (frac >= lo) & (frac < hi)
        if not sel.sum():
            continue
        cells = ""
        for name in CODECS:
            d = np.array([r[f"mask::{name}"] / r[f"orig::{name}"] - 1
                          for r in rows])[sel]
            cells += f"{d.mean() * 100:+15.1f}%"
        print(f"{lo:.0%}-{hi:.0%}".rjust(12) + f"{sel.sum():5d}" + cells)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(rows, open(a.out, "w"), indent=2)
    print(f"\nwrote {a.out} ({len(rows)} tiles)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
