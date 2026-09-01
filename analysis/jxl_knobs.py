#!/usr/bin/env python3
"""What each JPEG XL knob actually buys, measured on real device tiles.

    analysis/jxl_knobs.py

The module sets only JXL_ENC_FRAME_SETTING_EFFORT; distance and lossless are
commented out at jpegxl_module.c:57-61. So the three "encoding effort levels"
currently vary how hard the encoder searches at a *fixed* quality target -- they
change size and time, but every level decodes to the same picture. The paper
describes something different (III-C: low effort "high compression, low
quality", high effort "lossless"), so the implementation and the description do
not currently agree.

This measures the two knobs separately so the effort levels can be defined on
the one that actually trades quality.
"""

import argparse
import json
import os
import sys
import time

import numpy as np
import imagecodecs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from device_labelled_eval import calibrated_tiles, DATA   # noqa: E402


def psnr(a, b):
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return float("inf") if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default="amager-no-clouds")
    ap.add_argument("--tiles", type=int, default=12)
    ap.add_argument("-o", "--out", default="analysis/stage4.5-models/jxl_knobs.json")
    a = ap.parse_args()

    tiles = calibrated_tiles(os.path.join(DATA, f"{a.scene}.bayerRG"))
    rng = np.random.default_rng(43)
    sel = [tiles[i] for i in rng.permutation(len(tiles))[:a.tiles]]
    raw = sel[0].nbytes
    print(f"{len(sel)} tiles from {a.scene}, {raw:,} B each raw\n")

    rows = []
    print(f"{'distance':>9}{'effort':>7}{'bytes':>9}{'ratio':>7}{'ms':>8}{'PSNR dB':>9}")
    print("-" * 49)
    for dist in (0.0, 0.5, 1.0, 2.0, 3.0, 5.0):
        for eff in (1, 4, 7):
            sizes, times, qs = [], [], []
            for t in sel:
                kw = dict(effort=eff)
                if dist == 0.0:
                    kw["lossless"] = True
                else:
                    kw["distance"] = dist
                t0 = time.perf_counter()
                blob = imagecodecs.jpegxl_encode(t, **kw)
                times.append((time.perf_counter() - t0) * 1000)
                sizes.append(len(blob))
                qs.append(psnr(t, imagecodecs.jpegxl_decode(blob)))
            r = {"distance": dist, "effort": eff, "bytes": float(np.mean(sizes)),
                 "ms": float(np.mean(times)), "psnr": float(np.mean(qs))}
            rows.append(r)
            q = "lossless" if np.isinf(r["psnr"]) else f"{r['psnr']:8.1f}"
            print(f"{dist:9.1f}{eff:7}{r['bytes']:9.0f}{raw / r['bytes']:6.1f}x"
                  f"{r['ms']:8.1f}{q:>9}")
        print()

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(rows, open(a.out, "w"), indent=2)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
