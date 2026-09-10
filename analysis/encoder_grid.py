#!/usr/bin/env python3
"""Grid-search the encoder rungs for both codecs, at a fixed byte budget.

    analysis/encoder_grid.py --target 3000 --tiles 8

What this is choosing between. An effort level is only worth scheduling if it
differs in latency and energy -- those are the only quantities the scheduler can
see. The deployed JPEG XL ladder failed that test: it varied only search effort
at a fixed quality target, so effort 1/4/7 produced 4387/4374/4555 bytes, and
the top rung cost 9x the time for a 4% LARGER file.

Fixing the byte budget and varying effort is the design that gives the scheduler
something real to trade: downlink volume stays constant, so the choice is purely
compute-for-quality. This searches the grid for the three rungs that best
realise that, for JPEG XL and JPEG 2000 alike.

The JPEG 2000 side also measures RECOVERABILITY, and the measured answer is not
the one the standard's feature list suggests.

With CCSDS-style framing the radio does FEC and a per-frame CRC, so corrupted
frames are dropped rather than delivered corrupt: the application sees whole
frames missing at known offsets, not bit errors. Measured at a matched ~3000 B
budget and matched quality, losing one 512 B frame:

    JPEG XL      0/8 tiles decoded  -- the tile is gone
    JPEG 2000    8/8 tiles decoded  -- 47.6 dB degrades to 38.7 dB

That difference needs no resilience features at all. SOP/EPH markers and
tile-parts, the features nominally *for* this, make it strictly worse with
openjpeg's decoder: they add headers whose loss is fatal, so a hole that plain
codestreams survive becomes unrecoverable, and they cost 8-21% more bytes.

Truncation fails for both codecs regardless of quality layers or tile-parts,
because the tile-part header declares a length a short stream cannot satisfy.
Progressive-by-truncation is therefore not available here, whatever the format
supports in principle.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np
import imagecodecs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from device_labelled_eval import calibrated_tiles, DATA   # noqa: E402


def psnr(a, b):
    if a.shape != b.shape:
        return float("nan")
    m = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return float("inf") if m == 0 else 10 * np.log10(255.0 ** 2 / m)


# ----------------------------------------------------------------- JPEG XL --

def jxl_encode(tile, effort, distance):
    return imagecodecs.jpegxl_encode(tile, effort=effort, distance=distance)


def bisect_distance(tiles, effort, target, lo=0.3, hi=12.0, iters=10):
    """Distance that puts mean output at `target` bytes for this effort."""
    for _ in range(iters):
        mid = (lo + hi) / 2
        n = np.mean([len(jxl_encode(t, effort, mid)) for t in tiles])
        if n > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# --------------------------------------------------------------- JPEG 2000 --

def write_ppm(path, tile):
    h, w, _ = tile.shape
    with open(path, "wb") as f:
        f.write(f"P6\n{w} {h}\n255\n".encode())
        f.write(np.ascontiguousarray(tile).tobytes())


def read_ppm(path):
    with open(path, "rb") as f:
        assert f.readline().strip() == b"P6"
        line = f.readline()
        while line.startswith(b"#"):
            line = f.readline()
        w, h = map(int, line.split())
        f.readline()
        return np.frombuffer(f.read(), np.uint8).reshape(h, w, 3)


def opj_encode(tile, workdir, rate, resolutions=6, layers=1,
               sop=False, eph=False, cblk=None, extra=None):
    """Encode via opj_compress; returns (bytes, encode_seconds)."""
    src = os.path.join(workdir, "in.ppm")
    dst = os.path.join(workdir, "out.j2k")
    write_ppm(src, tile)
    if os.path.exists(dst):
        os.remove(dst)
    # -r takes one ratio per quality layer, coarsest first.
    rates = ",".join(f"{rate * (layers - i) / layers:g}" for i in range(layers))
    cmd = ["opj_compress", "-i", src, "-o", dst, "-r", rates,
           "-n", str(resolutions), "-I"]
    if sop:
        cmd.append("-SOP")
    if eph:
        cmd.append("-EPH")
    if cblk:
        cmd += ["-b", cblk]
    if extra:
        cmd += extra
    t0 = time.perf_counter()
    r = subprocess.run(cmd, capture_output=True)
    dt = time.perf_counter() - t0
    if r.returncode != 0 or not os.path.exists(dst):
        return None, dt
    return open(dst, "rb").read(), dt


def opj_decode(blob, workdir):
    src = os.path.join(workdir, "dec.j2k")
    dst = os.path.join(workdir, "dec.ppm")
    open(src, "wb").write(blob)
    if os.path.exists(dst):
        os.remove(dst)
    r = subprocess.run(["opj_decompress", "-i", src, "-o", dst],
                       capture_output=True)
    if r.returncode != 0 or not os.path.exists(dst):
        return None
    try:
        return read_ppm(dst)
    except Exception:
        return None


def bisect_rate(tiles, workdir, target, resolutions=6, layers=1,
                sop=False, eph=False, lo=2.0, hi=400.0, iters=9):
    for _ in range(iters):
        mid = (lo + hi) / 2
        sizes = []
        for t in tiles:
            b, _ = opj_encode(t, workdir, mid, resolutions, layers, sop, eph)
            sizes.append(len(b) if b else 10 ** 9)
        n = np.mean(sizes)
        if n > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ------------------------------------------------------------- resilience --

def drop_frames(blob, n_lost, chunk=512, seed=0):
    """Lose whole frame-sized chunks, zero-filled at the correct offset.

    That is what a receiver produces when a CRC-checked frame fails to arrive:
    it knows which frame is missing and leaves a hole, rather than shifting
    everything after it. An earlier version of this used a per-chunk drop
    PROBABILITY, which on a 3 kB tile spanning two 1 kB chunks selected nothing
    at 2% and reported damage-free results as evidence of resilience.
    """
    n = max(1, len(blob) // chunk)
    if n_lost <= 0:
        return blob
    rng = np.random.default_rng(seed)
    lost = set(rng.choice(n, size=min(n_lost, n), replace=False).tolist())
    out = bytearray()
    for i in range(n):
        piece = blob[i * chunk:(i + 1) * chunk]
        out += bytes(len(piece)) if i in lost else piece
    out += blob[n * chunk:]
    return bytes(out)


def truncate(blob, frac):
    """Prefix, closed with an end-of-codestream marker.

    Note this does not make JPEG 2000 truncation work: the tile-part header
    declares a length, so a short stream fails validation regardless, with or
    without quality layers or tile-parts. Measured, not assumed.
    """
    return blob[:max(2, int(len(blob) * frac))] + b"\xff\xd9"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=3000, help="bytes per tile")
    ap.add_argument("--tiles", type=int, default=8)
    ap.add_argument("--scene", default="amager-no-clouds")
    ap.add_argument("-o", "--out", default="analysis/stage4.5-models/encoder_grid.json")
    a = ap.parse_args()

    if not shutil.which("opj_compress"):
        sys.exit("opj_compress not found")

    tl = calibrated_tiles(os.path.join(DATA, f"{a.scene}.bayerRG"))
    rng = np.random.default_rng(43)
    tiles = [tl[i] for i in rng.permutation(len(tl))[:a.tiles]]
    work = tempfile.mkdtemp(prefix="encgrid-")
    result = {"target_bytes": a.target, "tiles": len(tiles), "jxl": [], "j2k": []}

    print(f"{len(tiles)} device tiles, {tiles[0].nbytes:,} B raw, "
          f"target {a.target} B/tile\n")

    # --- JPEG XL: effort sweep at fixed rate --------------------------------
    print("JPEG XL -- distance tuned per effort to hold the byte budget")
    print(f"{'effort':>7}{'distance':>10}{'bytes':>8}{'PSNR':>8}{'ms':>9}{'x e1':>7}")
    base = None
    for e in range(1, 10):
        d = bisect_distance(tiles, e, a.target)
        b, q, t = [], [], []
        for x in tiles:
            t0 = time.perf_counter(); blob = jxl_encode(x, e, d)
            t.append((time.perf_counter() - t0) * 1000)
            b.append(len(blob)); q.append(psnr(x, imagecodecs.jpegxl_decode(blob)))
        ms = float(np.mean(t)); base = base or ms
        row = {"effort": e, "distance": round(d, 3), "bytes": float(np.mean(b)),
               "psnr": float(np.mean(q)), "ms": ms, "rel": ms / base}
        result["jxl"].append(row)
        print(f"{e:7}{d:10.2f}{np.mean(b):8.0f}{np.mean(q):8.2f}{ms:9.1f}{ms/base:6.1f}x")

    # --- JPEG 2000: cost knobs at fixed rate --------------------------------
    # Fewer decomposition levels cost MORE time (larger subbands to code), so
    # resolutions runs the opposite way to intuition and is a genuine lever.
    print("\nJPEG 2000 -- cost knobs at the same byte budget")
    print(f"{'res':>5}{'layers':>7}{'cblk':>9}{'bytes':>8}{'PSNR':>8}{'ms':>9}")
    cfgs = [(r, l, cb) for r in (1, 2, 3, 6) for l in (1, 5) for cb in (None, "64,64")]
    for res, lay, cb in cfgs:
        rate = bisect_rate(tiles, work, a.target, res, lay)
        b, q, t = [], [], []
        for x in tiles:
            blob, dt = opj_encode(x, work, rate, res, lay, cblk=cb)
            if blob is None:
                continue
            dec = opj_decode(blob, work)
            b.append(len(blob)); t.append(dt * 1000)
            q.append(psnr(x, dec) if dec is not None else float("nan"))
        if not b:
            print(f"{res:5}{lay:7}{str(cb):>9}   failed"); continue
        row = {"resolutions": res, "layers": lay, "cblk": cb, "rate": round(rate, 2),
               "bytes": float(np.mean(b)), "psnr": float(np.nanmean(q)),
               "ms": float(np.mean(t))}
        result["j2k"].append(row)
        print(f"{res:5}{lay:7}{str(cb):>9}{np.mean(b):8.0f}{np.nanmean(q):8.2f}"
              f"{np.mean(t) :9.1f}")

    # --- what resilience costs ----------------------------------------------
    print("\nResilience at the same byte budget (JPEG 2000)")
    print(f"{'config':>24}{'bytes':>8}{'PSNR':>8}{'trunc75%':>10}{'lose1':>9}{'lose2':>9}")
    res_rows = []
    for name, res, lay, sop, eph in (("plain res6 L1", 6, 1, False, False),
                                     ("SOP+EPH res6 L1", 6, 1, True, True),
                                     ("layers=5", 6, 5, False, False),
                                     ("SOP+EPH layers=5", 6, 5, True, True)):
        rate = bisect_rate(tiles, work, a.target, res, lay, sop, eph)
        sizes, base_q, tr, er2, er5 = [], [], [], [], []
        for x in tiles:
            blob, _ = opj_encode(x, work, rate, res, lay, sop, eph)
            if blob is None:
                continue
            sizes.append(len(blob))
            d0 = opj_decode(blob, work); base_q.append(psnr(x, d0) if d0 is not None else np.nan)
            dt_ = opj_decode(truncate(blob, 0.75), work)
            tr.append(psnr(x, dt_) if dt_ is not None else np.nan)
            for frac, acc in ((1, er2), (2, er5)):
                dd = opj_decode(drop_frames(blob, frac, seed=1), work)
                acc.append(psnr(x, dd) if dd is not None else np.nan)
        if not sizes:
            print(f"{name:>24}   failed"); continue
        row = {"config": name, "sop": sop, "eph": eph, "layers": lay,
               "bytes": float(np.mean(sizes)), "psnr": float(np.nanmean(base_q)),
               "trunc75": float(np.nanmean(tr)), "erase2": float(np.nanmean(er2)),
               "erase5": float(np.nanmean(er5)),
               "trunc75_ok": float(np.mean(~np.isnan(tr))),
               "erase2_ok": float(np.mean(~np.isnan(er2))),
               "erase5_ok": float(np.mean(~np.isnan(er5)))}
        res_rows.append(row)
        def f(v): return "fail" if np.isnan(v) else f"{v:.1f}"
        print(f"{name:>24}{np.mean(sizes):8.0f}{np.nanmean(base_q):8.2f}"
              f"{f(np.nanmean(tr)):>10}{f(np.nanmean(er2)):>9}{f(np.nanmean(er5)):>9}")
    result["resilience"] = res_rows

    # JPEG XL for comparison: it has no resynchronisation mechanism at all.
    print("\nJPEG XL under the same damage (no resync mechanism exists)")
    d = bisect_distance(tiles, 5, a.target)
    ok_t = ok_e = 0
    for x in tiles:
        blob = jxl_encode(x, 5, d)
        for fn, key in ((lambda b: truncate(b, 0.75), "t"),
                        (lambda b: drop_frames(b, 1, seed=1), "e")):
            try:
                dec = imagecodecs.jpegxl_decode(fn(blob))
                if dec is not None and dec.shape == x.shape:
                    if key == "t": ok_t += 1
                    else: ok_e += 1
            except Exception:
                pass
    print(f"  truncated to 75%: {ok_t}/{len(tiles)} decoded")
    print(f"  one frame lost:  {ok_e}/{len(tiles)} decoded")
    result["jxl_resilience"] = {"trunc75_ok": ok_t / len(tiles),
                                "erase2_ok": ok_e / len(tiles)}

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(result, open(a.out, "w"), indent=2)
    shutil.rmtree(work, ignore_errors=True)
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
