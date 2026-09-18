#!/usr/bin/env python3
"""Encoder quality on the CloudSEN12 corpus -- both codecs, the full knob space.

    analysis/encoder_corpus_eval.py --tiles 300            # ~1 h on 8 cores
    analysis/encoder_corpus_eval.py --report analysis/stage4.5-models/encoder_corpus.json

Why. Every encoder PSNR so far came from 8 tiles of one device frame. The
quality axis of an encoder rung has to come from the corpus like every other
stage's, and it has to be an axis on which the rungs actually differ: for
JPEG 2000 that is degraded-mode PSNR at fixed bytes (layers buy resilience,
not fidelity); for JPEG XL it is whatever the rate-distortion curves per effort
say it is -- at 3 kB on 8 device tiles effort gave two plateaus, and whether
that holds on the corpus, at other budgets, is what this measures.

What the encoder sees. Tiles retained by the pipeline: device-footprint 224 px
crops rendered at the frozen gain, kept when >50% clear by the expert mask
(threshold_percentage 0.5), cloud pixels replaced by a 5x5 Gaussian-blurred
fill (mask_lowpass 5) -- the segmentation module's own output rule.

Two codecs, two sweeps:
  JXL   effort 1..9 x distance {0.5..8}: bytes, PSNR, host encode time per
        tile. Analysis interpolates each tile's (bytes, PSNR) curve per effort
        at fixed byte budgets, so efforts compare at iso-rate and PAIRED.
  J2K   budget {3, 6, 10 kB} x layers {1..8}: rate bisected per (budget,
        layers) on a subset, then per tile: bytes, clean PSNR, degraded PSNR
        with 1 and 2 CCSDS frames (512 B) lost averaged over K drop patterns,
        decode rate. Resync markers on, resolutions 6 (the settled knobs).

Host libjxl / openjpeg are not the board's versions; bytes and PSNR are the
codec's, cost is the board's (tools/phase3_enc_board.sh). The board bench
reports bytes per tile too, which is the cross-check.
"""

import argparse
import collections
import glob
import json
import os
import sys
import tempfile
import time
from multiprocessing import Pool

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = os.path.expanduser("~/dipp-datasets/cloudsen12")
THICK, THIN, SHADOW = 1, 2, 3
FRAME = 512
EFFORTS = list(range(1, 10))
DISTANCES = [0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]
BUDGETS = [3000, 6000, 10000]
LAYERS = [1, 2, 3, 4, 5, 6, 8]
K_DROP = 12


# ---------------------------------------------------------------- corpus --

def corpus(n, seed=43, split="validation", gain=0.050, crop=224, min_clear=0.5):
    """Retained tiles as the encoder receives them."""
    import cv2
    fs = sorted(glob.glob(os.path.join(DATA, split, "*.npz")))
    rng = np.random.default_rng(seed)
    offs = [(y, x) for y in (0, 509 - crop) for x in (0, 509 - crop)]
    cands = [(f, o) for f in fs for o in offs]
    rng.shuffle(cands)
    tiles, meta = [], []
    for f, (y0, x0) in cands:
        with np.load(f) as z:
            rgb = np.transpose(z["rgb"], (1, 2, 0))[y0:y0 + crop, x0:x0 + crop]
            lab = z["label"][y0:y0 + crop, x0:x0 + crop]
        cloud = (lab == THICK) | (lab == THIN)          # shadow stays ground
        clear = 1.0 - cloud.mean()
        if clear <= min_clear:
            continue
        t = np.clip(rgb.astype(np.float32) * gain, 0, 255).astype(np.uint8)
        if cloud.any():
            blurred = cv2.GaussianBlur(t, (5, 5), 0)
            t = np.where(cloud[..., None], blurred, t)
        tiles.append(np.ascontiguousarray(t))
        meta.append(dict(scene=os.path.basename(f)[:-4], y0=int(y0), x0=int(x0),
                         clear=float(clear)))
        if len(tiles) >= n:
            break
    return tiles, meta


def psnr(a, b):
    m = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return 99.0 if m == 0 else float(10 * np.log10(255.0 ** 2 / m))


# ------------------------------------------------------------------- JXL --

def jxl_tile(args):
    """One tile through every (effort, distance): [(bytes, psnr, sec), ...]."""
    import imagecodecs
    t, = args
    out = []
    for e in EFFORTS:
        for d in DISTANCES:
            t0 = time.perf_counter()
            blob = imagecodecs.jpegxl_encode(t, effort=e, distance=d, numthreads=1)
            dt = time.perf_counter() - t0
            dec = imagecodecs.jpegxl_decode(blob, numthreads=1)
            out.append((len(blob), psnr(t, dec), dt))
    return out


# ------------------------------------------------------------------- J2K --

def _opj(tile, work, rate, layers, sop=True, eph=True, resolutions=6):
    from encoder_grid import opj_encode
    return opj_encode(tile, work, rate, resolutions, layers, sop, eph)


def j2k_bisect(args):
    """Rate that puts the subset's mean size at `budget` for `layers`."""
    tiles, budget, layers = args
    work = tempfile.mkdtemp(prefix="j2kb-")
    lo, hi = 2.0, 400.0
    for _ in range(10):
        mid = (lo + hi) / 2
        sizes = []
        for t in tiles:
            b, _ = _opj(t, work, mid, layers)
            sizes.append(len(b) if b else 10 ** 9)
        if np.mean(sizes) > budget:
            lo = mid
        else:
            hi = mid
    return (budget, layers, (lo + hi) / 2)


def j2k_tile(args):
    """One tile through every (budget, layers) at the bisected rates."""
    from encoder_grid import opj_decode, drop_frames
    t, rates = args
    work = tempfile.mkdtemp(prefix="j2kt-")
    out = []
    for (budget, layers), rate in rates:
        blob, dt = _opj(t, work, rate, layers)
        if blob is None:
            out.append(dict(budget=budget, layers=layers, bytes=None)); continue
        dec = opj_decode(blob, work)
        clean = psnr(t, dec) if dec is not None else float("nan")
        deg = {}
        for k in (1, 2):
            vals, ok = [], 0
            for s in range(K_DROP):
                d = opj_decode(drop_frames(blob, k, FRAME, seed=s), work)
                if d is not None:
                    ok += 1; vals.append(psnr(t, d))
            deg[k] = (float(np.mean(vals)) if vals else float("nan"), ok / K_DROP)
        out.append(dict(budget=budget, layers=layers, bytes=len(blob), sec=dt,
                        psnr=clean, deg1=deg[1][0], dec1=deg[1][1],
                        deg2=deg[2][0], dec2=deg[2][1]))
    return out


# --------------------------------------------------------------- analysis --

def ci_diff(a, b, n=2000, seed=43):
    """95% CI of mean(a-b), paired bootstrap over tiles."""
    d = np.asarray(a, float) - np.asarray(b, float)
    d = d[np.isfinite(d)]
    rng = np.random.default_rng(seed)
    m = [rng.choice(d, len(d)).mean() for _ in range(n)]
    return float(np.mean(d)), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def jxl_at_budget(R, budget):
    """Per effort: per-tile PSNR interpolated at `budget` bytes (log-bytes)."""
    B, P = R["jxl_bytes"], R["jxl_psnr"]           # [tile][effort][distance]
    out = {}
    for ei, e in enumerate(EFFORTS):
        vals = []
        for ti in range(len(B)):
            b = np.array(B[ti][ei], float); p = np.array(P[ti][ei], float)
            o = np.argsort(b); b, p = b[o], p[o]
            if budget < b[0] or budget > b[-1]:
                vals.append(np.nan); continue
            vals.append(float(np.interp(np.log(budget), np.log(b), p)))
        out[e] = np.array(vals)
    return out


def report(R):
    n = len(R["jxl_bytes"])
    print(f"corpus: {n} retained tiles ({R['split']}, gain {R['gain']})\n")
    print("=== JPEG XL: PSNR at fixed bytes, per effort (per-tile interpolation) ===")
    for budget in BUDGETS:
        at = jxl_at_budget(R, budget)
        cov = {e: int(np.isfinite(v).sum()) for e, v in at.items()}
        print(f"--- {budget} B/tile  (tiles inside the curve: "
              f"{min(cov.values())}-{max(cov.values())} of {n})")
        prev = None
        for e in EFFORTS:
            v = at[e]; m = float(np.nanmean(v))
            step = ""
            if prev is not None:
                mu, lo, hi = ci_diff(v, at[prev])
                step = f"  vs e{prev}: {mu:+.3f} dB [{lo:+.3f}, {hi:+.3f}]" + \
                       ("" if lo > 0 else "   NOT above noise")
            t = float(np.mean([R["jxl_sec"][ti][e - 1][2] for ti in range(n)]))
            print(f"  e{e}: {m:6.2f} dB   host {t*1000:6.1f} ms (d=1){step}")
            prev = e
    print("\n=== JPEG XL: bytes and PSNR at fixed distance ===")
    for d_i, d in enumerate(DISTANCES):
        if d not in (1.0, 2.0, 4.0):
            continue
        row = []
        for ei, e in enumerate(EFFORTS):
            bb = np.mean([R["jxl_bytes"][ti][ei][d_i] for ti in range(n)])
            pp = np.mean([R["jxl_psnr"][ti][ei][d_i] for ti in range(n)])
            row.append(f"e{e} {bb:5.0f}B/{pp:5.2f}dB")
        print(f"  d={d}: " + "  ".join(row))
    print("\n=== JPEG 2000: layers at fixed bytes (resync on, res 6) ===")
    J = R["j2k"]   # [tile][config]
    cfgs = [(c["budget"], c["layers"]) for c in J[0]]
    for budget in BUDGETS:
        print(f"--- {budget} B/tile")
        prev = None
        for layers in LAYERS:
            ci = cfgs.index((budget, layers))
            rows = [J[ti][ci] for ti in range(n) if J[ti][ci].get("bytes")]
            if not rows:
                continue
            b = np.mean([r["bytes"] for r in rows]); p = np.nanmean([r["psnr"] for r in rows])
            d1 = np.array([r["deg1"] for r in rows]); d2 = np.array([r["deg2"] for r in rows])
            dr1 = np.mean([r["dec1"] for r in rows]); dr2 = np.mean([r["dec2"] for r in rows])
            t = np.mean([r["sec"] for r in rows]) * 1000
            step = ""
            if prev is not None:
                pj = cfgs.index((budget, prev))
                pd1 = np.array([J[ti][pj]["deg1"] for ti in range(n) if J[ti][pj].get("bytes")])
                if len(pd1) == len(d1):
                    mu, lo, hi = ci_diff(d1, pd1)
                    step = f"  deg1 vs L{prev}: {mu:+.2f} [{lo:+.2f}, {hi:+.2f}]" + \
                           ("" if lo > 0 else "   NOT above noise")
            print(f"  L{layers}: {b:6.0f} B  clean {p:5.2f} dB  1 lost {np.nanmean(d1):5.2f} dB "
                  f"(dec {100*dr1:3.0f}%)  2 lost {np.nanmean(d2):5.2f} dB (dec {100*dr2:3.0f}%)"
                  f"  host {t:5.1f} ms{step}")
            prev = layers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiles", type=int, default=300)
    ap.add_argument("--seed", type=int, default=43)
    ap.add_argument("--split", default="validation", choices=["validation", "test"])
    ap.add_argument("--gain", type=float, default=0.050)
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--skip-jxl", action="store_true")
    ap.add_argument("--skip-j2k", action="store_true")
    ap.add_argument("--report", help="print the analysis of an existing JSON and exit")
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "stage4.5-models",
                                                        "encoder_corpus.json"))
    a = ap.parse_args()
    if a.report:
        report(json.load(open(a.report))); return 0

    t0 = time.time()
    tiles, meta = corpus(a.tiles, a.seed, a.split, a.gain)
    print(f"{len(tiles)} retained tiles from {a.split} (seed {a.seed}), "
          f"mean clear {np.mean([m['clear'] for m in meta]):.3f}", flush=True)
    R = dict(split=a.split, gain=a.gain, seed=a.seed, n=len(tiles), meta=meta,
             efforts=EFFORTS, distances=DISTANCES, budgets=BUDGETS, layers=LAYERS,
             k_drop=K_DROP, frame=FRAME)
    with Pool(a.workers) as pool:
        if not a.skip_jxl:
            res = pool.map(jxl_tile, [(t,) for t in tiles], chunksize=4)
            R["jxl_bytes"] = [[[r[ei * len(DISTANCES) + di][0] for di in range(len(DISTANCES))]
                               for ei in range(len(EFFORTS))] for r in res]
            R["jxl_psnr"] = [[[r[ei * len(DISTANCES) + di][1] for di in range(len(DISTANCES))]
                              for ei in range(len(EFFORTS))] for r in res]
            R["jxl_sec"] = [[[r[ei * len(DISTANCES) + di][2] for di in range(len(DISTANCES))]
                             for ei in range(len(EFFORTS))] for r in res]
            print(f"JXL done in {time.time()-t0:.0f} s", flush=True)
            json.dump(R, open(a.out, "w"))
        if not a.skip_j2k:
            sub = tiles[:32]
            rates = pool.map(j2k_bisect, [(sub, b, l) for b in BUDGETS for l in LAYERS])
            rate_map = [((b, l), r) for b, l, r in rates]
            R["j2k_rates"] = [dict(budget=b, layers=l, rate=r) for b, l, r in rates]
            print("J2K rates: " + "  ".join(f"{b}/L{l}={r:.1f}" for b, l, r in rates), flush=True)
            R["j2k"] = pool.map(j2k_tile, [(t, rate_map) for t in tiles], chunksize=2)
            print(f"J2K done in {time.time()-t0:.0f} s", flush=True)
    json.dump(R, open(a.out, "w"))
    print(f"wrote {a.out}\n")
    report(R)
    return 0


if __name__ == "__main__":
    sys.exit(main())
