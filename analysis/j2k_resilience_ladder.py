#!/usr/bin/env python3
"""Can JPEG 2000 buy RESILIENCE with compute, at a fixed byte budget?

    analysis/j2k_resilience_ladder.py --target 3000

Quality layers are exhausted as a ladder: past ~5 they cost compute and destroy
degraded-mode fidelity (40.5 dB at 5 layers, 21.1 dB at 12), because layer
signalling eats a fixed budget and starves the base layer. But layers were never
JPEG 2000's error-resilience mechanism -- these are:

  RESET (2)    reset context probabilities each coding pass
  TERMALL (4)  terminate EVERY coding pass independently, so a damaged pass
               does not poison the ones after it
  VSC (8)      vertically causal context: no dependency on the row below
  ERTERM (16)  predictable termination, so a decoder can detect a bad pass
  SEGSYM (32)  segmentation symbol at each bit-plane end, for error detection

plus two partitioning knobs: smaller PRECINCTS cut the image into more
independently addressable packets, and TILES make regions independently
decodable outright. All of them cost bytes and encoder time and buy
recoverability -- which is the trade the scheduler is supposed to be making.

Resilience is scored the way the link actually fails: whole 512 B frames are
erased (CCSDS framing plus a per-frame CRC means the receiver drops bad frames
rather than passing them through), averaged over many drop patterns because a
3 kB tile is only ~6 frames and which frame dies matters more than how many.
"""

import argparse
import json
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from encoder_grid import (psnr, opj_encode, opj_decode, bisect_rate,   # noqa
                          drop_frames)
from device_labelled_eval import calibrated_tiles, DATA                # noqa

# name, layers, cblk_style(-M), precinct(-c), tile(-t)
LADDER = [
    # At 3 kB more layers inverted (degraded fidelity collapsed past 5) because
    # layer signalling starved the base layer. At 10 kB there is room, so the
    # question reopens: how many layers stay affordable, and does prec128 --
    # the only partitioning that fits -- still pay on top of them? Tiles are
    # dropped: they LOWER decode rate here, because a lost frame carrying a
    # tile header destroys that whole tile.
    ("L1 plain",          1,  0,  None,        None),
    ("L3 plain",          3,  0,  None,        None),
    ("L6 plain",          6,  0,  None,        None),
    ("L12 plain",         12, 0,  None,        None),
    ("L3 prec128",        3,  46, "[128,128]", None),
    ("L6 prec128",        6,  46, "[128,128]", None),
    ("L12 prec128",       12, 46, "[128,128]", None),
    ("L20 prec128",       20, 46, "[128,128]", None),
]


def extras(mode, prec, tile):
    e = []
    if mode:
        e += ["-M", str(mode)]
    if prec:
        e += ["-c", prec]
    if tile:
        e += ["-t", tile]
    return e or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=3000)
    ap.add_argument("--tiles", type=int, default=6)
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--scene", default="amager-no-clouds")
    ap.add_argument("--resolutions", type=int, default=6)
    ap.add_argument("-o", "--out",
                    default="analysis/stage4.5-models/j2k_resilience_ladder.json")
    a = ap.parse_args()

    tl = calibrated_tiles(os.path.join(DATA, f"{a.scene}.bayerRG"))
    rng = np.random.default_rng(43)
    tiles = [tl[i] for i in rng.permutation(len(tl))[:a.tiles]]
    work = tempfile.mkdtemp(prefix="j2kres-")
    out = {"target_bytes": a.target, "tiles": len(tiles), "rungs": []}
    print(f"{len(tiles)} device tiles, target {a.target} B/tile, "
          f"{a.seeds} drop patterns\n", flush=True)
    print(f"{'config':20} {'B/tile':>8} {'PSNR':>7} {'ms':>7} "
          f"{'lost1':>14} {'lost2':>14}")

    for name, lay, mode, prec, tile in LADDER:
        ex = extras(mode, prec, tile)
        hi = max(4000.0, 400.0 * lay)
        rate = bisect_rate(tiles, work, a.target, a.resolutions, lay,
                           sop=True, eph=True, hi=hi, iters=12)
        b, q, ms = [], [], []
        blobs = []
        for x in tiles:
            blob, dt = opj_encode(x, work, rate, a.resolutions, lay,
                                  True, True, extra=ex)
            if blob is None:
                continue
            blobs.append((x, blob))
            b.append(len(blob)); ms.append(dt * 1000)
            dec = opj_decode(blob, work)
            q.append(psnr(x, dec) if dec is not None else float("nan"))

        surv = {}
        for nlost in (1, 2):
            got, ok, trials = [], 0, 0
            for seed in range(a.seeds):
                for x, blob in blobs:
                    trials += 1
                    dec = opj_decode(drop_frames(blob, nlost, seed=seed), work)
                    if dec is not None:
                        ok += 1; got.append(psnr(x, dec))
            surv[f"lost{nlost}"] = {
                "decode_rate": ok / trials if trials else float("nan"),
                "psnr": float(np.nanmean(got)) if got else float("nan")}

        r = {"name": name, "layers": lay, "cblk_style": mode,
             "precinct": prec, "tile": tile, "rate": rate,
             "bytes": float(np.mean(b)) if b else float("nan"),
             "psnr": float(np.nanmean(q)) if q else float("nan"),
             "host_ms": float(np.median(ms)) if ms else float("nan"),
             "survival": surv}
        out["rungs"].append(r)
        s1, s2 = surv["lost1"], surv["lost2"]
        print(f"{name:20} {r['bytes']:8.0f} {r['psnr']:7.2f} {r['host_ms']:7.1f} "
              f"{100*s1['decode_rate']:4.0f}% {s1['psnr']:7.1f}dB "
              f"{100*s2['decode_rate']:4.0f}% {s2['psnr']:7.1f}dB", flush=True)

    json.dump(out, open(a.out, "w"), indent=1)
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
