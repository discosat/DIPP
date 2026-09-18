#!/usr/bin/env python3
"""The JPEG 2000 quality-layer ladder, at a fixed byte budget.

    analysis/j2k_layer_ladder.py --target 3000 --tiles 8

Why layers and not resolutions. At iso-rate the decomposition count moves PSNR
a long way -- 32.5 dB at 1 level to 47.6 dB at 6 -- but it moves it the WRONG
way for a ladder: res=6 is simultaneously the best quality and the cheapest,
because fewer levels leave larger subbands to code. A knob whose best setting is
also its fastest is strictly dominated and can never justify spending effort, so
resolutions is fixed at 6 and is not a rung.

Quality layers are the opposite shape. They leave PSNR-at-full-receipt flat
(47.57 dB at 1 layer, 47.37 at 5) while costing real compute, and what they buy
is not fidelity but GRACEFUL DEGRADATION: the codestream becomes SNR-scalable,
so a downlink that stops early still decodes at a lower quality instead of
failing. That is the property worth scheduling on for a satellite, and it is
invisible to any metric measured on a complete file -- which is why this script
scores each rung on partial receipt, not just on PSNR.
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=3000)
    ap.add_argument("--tiles", type=int, default=8)
    ap.add_argument("--scene", default="amager-no-clouds")
    ap.add_argument("--resolutions", type=int, default=6)
    ap.add_argument("--layers", default="1,2,3,5")
    ap.add_argument("--seeds", type=int, default=24,
                    help="drop patterns averaged per loss count")
    ap.add_argument("-o", "--out",
                    default="analysis/stage4.5-models/j2k_layer_ladder.json")
    a = ap.parse_args()

    tl = calibrated_tiles(os.path.join(DATA, f"{a.scene}.bayerRG"))
    rng = np.random.default_rng(43)
    tiles = [tl[i] for i in rng.permutation(len(tl))[:a.tiles]]
    print(f"{len(tiles)} device tiles from {a.scene}, "
          f"target {a.target} B/tile, resolutions={a.resolutions}", flush=True)

    work = tempfile.mkdtemp(prefix="j2klad-")
    out = {"target_bytes": a.target, "tiles": len(tiles),
           "resolutions": a.resolutions, "scene": a.scene, "rungs": []}

    for lay in [int(x) for x in a.layers.split(",")]:
        # Resync markers on: the erasure case is the one that matters on a
        # CCSDS-framed link, and they cost ~1% of the budget.
        # The search ceiling has to scale with the layer count. With
        # cp_disto_alloc the LAST layer's rate is ratio/layers, so hitting a
        # fixed byte budget needs ratio ~= 49.8 * layers -- 598 at 12 layers,
        # 2390 at 48. A fixed hi=400 silently pins every high-layer config at
        # the ceiling and returns tiles 1.5-5.4x over budget, which reads as
        # "more layers cannot hold the budget" when it actually means "the
        # bisection was never allowed to look there". That wrong conclusion was
        # published in this file once; the factor of 4 headroom is deliberate.
        hi = max(400.0, 200.0 * lay)
        rate = bisect_rate(tiles, work, a.target, a.resolutions, lay,
                           sop=True, eph=True, hi=hi, iters=12)
        if rate > 0.95 * hi:
            print(f"  L={lay}: WARNING bisection near ceiling {hi:.0f} "
                  f"(rate={rate:.1f}) -- treat bytes as unconverged", flush=True)
        b, q, ms = [], [], []
        for x in tiles:
            blob, dt = opj_encode(x, work, rate, a.resolutions, lay, True, True)
            if blob is None:
                continue
            b.append(len(blob)); ms.append(dt * 1000)
            dec = opj_decode(blob, work)
            q.append(psnr(x, dec) if dec is not None else float("nan"))

        # Graceful degradation: what survives when part of the stream is gone.
        # This is the property layers actually buy, so it is measured per rung.
        #
        # Averaged over many drop patterns, and that is not a detail. A 3 kB
        # tile is only ~6 frames, and JPEG 2000 layers are CUMULATIVE
        # refinements: losing the first frame costs the base image, losing the
        # last costs the finest refinement. A single fixed seed therefore does
        # not measure the codec, it measures which frame that seed happened to
        # pick -- and with so few frames the two outcomes differ by tens of dB.
        # An undecodable tile is scored explicitly rather than dropped from the
        # mean, because silently averaging only the successes would report a
        # codec that fails half the time as if it were fine.
        surv = {}
        for nlost in (1, 2):
            got, ok, trials = [], 0, 0
            for seed in range(a.seeds):
                for x in tiles:
                    blob, _ = opj_encode(x, work, rate, a.resolutions, lay,
                                         True, True)
                    if blob is None:
                        continue
                    trials += 1
                    dec = opj_decode(drop_frames(blob, nlost, seed=seed), work)
                    if dec is not None:
                        ok += 1; got.append(psnr(x, dec))
            surv[f"lost{nlost}"] = {
                "decoded": ok, "of": trials,
                "decode_rate": ok / trials if trials else float("nan"),
                "psnr": float(np.nanmean(got)) if got else float("nan"),
                "psnr_p10": (float(np.nanpercentile(got, 10)) if got
                             else float("nan"))}

        rung = {"layers": lay, "rate": rate,
                "bytes": float(np.mean(b)), "psnr": float(np.nanmean(q)),
                "host_ms": float(np.median(ms)), "survival": surv}
        out["rungs"].append(rung)
        print(f"  L={lay:<2} rate={rate:7.2f}  {rung['bytes']:7.1f} B  "
              f"{rung['psnr']:6.2f} dB  host {rung['host_ms']:6.2f} ms  "
              f"lost1 {100*surv['lost1']['decode_rate']:3.0f}% "
              f"@{surv['lost1']['psnr']:5.1f} dB (p10 {surv['lost1']['psnr_p10']:5.1f})  "
              f"lost2 {100*surv['lost2']['decode_rate']:3.0f}% "
              f"@{surv['lost2']['psnr']:5.1f} dB (p10 {surv['lost2']['psnr_p10']:5.1f})",
              flush=True)

    json.dump(out, open(a.out, "w"), indent=1)
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
