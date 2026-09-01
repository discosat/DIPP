#!/usr/bin/env python3
"""JPEG XL vs JPEG 2000 on real device tiles: size, quality, time, and how each
degrades when the downlink loses part of the file.

    analysis/codec_compare.py

Two different robustness properties get conflated, so this separates them:

  * TRUNCATION -- the pass ends before the file finishes. A progressive
    codestream still renders what arrived.
  * ERASURE -- a frame is lost mid-file, leaving a hole. Progressive alone does
    not help; the decoder needs resynchronisation points to pick up after the
    gap. JPEG 2000 has SOP/EPH markers and independent precincts for exactly
    this; JPEG XL does not.

Which one matters depends on the link. CCSDS-style framing with FEC and a CRC
drops corrupted frames rather than delivering corrupt bytes, so the application
sees erasures, not bit flips -- and a pass that ends early is truncation.
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
    ap.add_argument("--tiles", type=int, default=10)
    ap.add_argument("-o", "--out", default="analysis/stage4.5-models/codec_compare.json")
    a = ap.parse_args()

    tiles = calibrated_tiles(os.path.join(DATA, f"{a.scene}.bayerRG"))
    rng = np.random.default_rng(43)
    sel = [tiles[i] for i in rng.permutation(len(tiles))[:a.tiles]]
    raw = sel[0].nbytes
    print(f"{len(sel)} tiles, {raw:,} B raw each\n")

    rows = []
    print(f"{'codec':>22}{'bytes':>9}{'ratio':>7}{'ms':>8}{'PSNR dB':>9}")
    print("-" * 55)

    def bench(label, enc, dec):
        sizes, times, qs = [], [], []
        for t in sel:
            t0 = time.perf_counter()
            blob = enc(t)
            times.append((time.perf_counter() - t0) * 1000)
            sizes.append(len(blob))
            qs.append(psnr(t, dec(blob)))
        r = {"codec": label, "bytes": float(np.mean(sizes)),
             "ms": float(np.mean(times)), "psnr": float(np.mean(qs))}
        rows.append(r)
        q = "lossless" if np.isinf(r["psnr"]) else f"{r['psnr']:8.1f}"
        print(f"{label:>22}{r['bytes']:9.0f}{raw / r['bytes']:6.1f}x"
              f"{r['ms']:8.1f}{q:>9}")
        return r

    bench("jxl d=1.0 e=4", lambda x: imagecodecs.jpegxl_encode(x, effort=4, distance=1.0),
          imagecodecs.jpegxl_decode)
    bench("jxl d=3.0 e=1", lambda x: imagecodecs.jpegxl_encode(x, effort=1, distance=3.0),
          imagecodecs.jpegxl_decode)
    bench("jxl lossless e=7", lambda x: imagecodecs.jpegxl_encode(x, effort=7, lossless=True),
          imagecodecs.jpegxl_decode)
    # JPEG 2000 'level' is the compression ratio target in imagecodecs.
    for lv in (20, 40, 80):
        bench(f"j2k ratio~{lv}", lambda x, lv=lv: imagecodecs.jpeg2k_encode(x, level=lv),
              imagecodecs.jpeg2k_decode)
    bench("j2k reversible", lambda x: imagecodecs.jpeg2k_encode(x, reversible=True),
          imagecodecs.jpeg2k_decode)

    print("\ntruncation behaviour (decode the first N% of the codestream):")
    print(f"{'codec':>22}{'50%':>10}{'75%':>10}{'90%':>10}")
    for label, enc in (("jxl d=1.0 e=4",
                        lambda x: imagecodecs.jpegxl_encode(x, effort=4, distance=1.0)),
                       ("j2k ratio~40",
                        lambda x: imagecodecs.jpeg2k_encode(x, level=40))):
        cells = []
        for keep in (0.5, 0.75, 0.9):
            ok = 0
            for t in sel:
                blob = enc(t)
                cut = blob[:int(len(blob) * keep)]
                try:
                    dec = (imagecodecs.jpegxl_decode if "jxl" in label
                           else imagecodecs.jpeg2k_decode)(cut)
                    if dec is not None and dec.shape == t.shape:
                        ok += 1
                except Exception:
                    pass
            cells.append(f"{ok}/{len(sel)}")
        print(f"{label:>22}{cells[0]:>10}{cells[1]:>10}{cells[2]:>10}")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(rows, open(a.out, "w"), indent=2)
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
