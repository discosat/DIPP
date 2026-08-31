#!/usr/bin/env python3
"""Reproduce the device's preprocessing chain on the host, then sweep variants.

    analysis/preprocess_replica.py --sweep
    analysis/preprocess_replica.py --dump-tiles 6

The device chain, from the module sources:

  emulate_camera  2056x2464, 12 bits in 16-bit containers, 1 channel Bayer
  demosaic_module cv2.cvtColor(COLOR_BayerRG2BGR) -> rotate 180 ->
                  cv2.normalize(0, 255, NORM_MINMAX), still uint16, BGR
  tile_module     bits_pixel==8 so take the LOW byte of each 16-bit container,
                  then crop 224x224 tiles: 11 across x 9 down = 99, bottom 40
                  rows dropped
  classification  memcpy straight into a [1,224,224,3] uint8 tensor

Each variant below isolates one suspected mismatch against how the models were
actually trained (EuroSAT RGB JPEGs, natively 64x64, bilinear-upscaled to 224,
raw 0-255). The point is not to guess which one matters but to measure it:
a variant earns a fix only if it changes the predictions.
"""

import argparse
import collections
import json
import os
import sys

import cv2
import numpy as np

H, W = 2056, 2464
TILE = 224
DATA = os.path.expanduser("~/dipp2-experiments/sentinel2-dataset")
CLASSES = ["AnnualCrop", "Forest", "HerbaceousVegetation", "Highway",
           "Industrial", "Pasture", "PermanentCrop", "Residential",
           "River", "SeaLake"]


def device_frame(path, bayer_code=cv2.COLOR_BayerRG2BGR, radiometry="minmax"):
    """Everything demosaic_module.cpp does, in order, to a full frame."""
    raw = np.fromfile(path, dtype="<u2")
    if raw.size != H * W:
        raise SystemExit(f"{path}: {raw.size} samples, expected {H*W}")
    raw = raw.reshape(H, W)

    dem = cv2.cvtColor(raw, bayer_code)              # -> uint16, 3 channels
    rot = cv2.rotate(dem, cv2.ROTATE_180)            # the 180 deg warpAffine

    if radiometry == "minmax":
        # cv::normalize(..., 0, 255, NORM_MINMAX) over all three channels
        # jointly -- one hot pixel sets the max for the whole frame.
        out = cv2.normalize(rot, None, 0, 255, cv2.NORM_MINMAX)
    elif radiometry == "fixed12":
        # Absolute 12-bit -> 8-bit. Scene-independent, so identical ground
        # radiance maps to identical pixel values in every frame.
        out = np.clip(rot.astype(np.int32) >> 4, 0, 255).astype(np.uint16)
    else:
        raise ValueError(radiometry)
    # tile_module takes the LOW byte of each 16-bit little-endian container
    return (out & 0xFF).astype(np.uint8)             # HxWx3, BGR


def tiles(frame, scale="native"):
    """The 99 tiles the tiler emits, optionally rescaled to the training domain."""
    if scale == "wide":
        # Match EuroSAT's ground extent rather than its pixel count: take a
        # window 3.5x wider and resize down, so a patch covers what a 64x64
        # EuroSAT patch covered.
        step = int(TILE * 3.5)
        for y in range(0, H - step + 1, step):
            for x in range(0, W - step + 1, step):
                yield cv2.resize(frame[y:y+step, x:x+step], (TILE, TILE),
                                 interpolation=cv2.INTER_AREA)
        return
    for y in range(0, H - TILE + 1, TILE):
        for x in range(0, W - TILE + 1, TILE):
            t = frame[y:y+TILE, x:x+TILE]
            if scale == "blur64":
                # Match EuroSAT's effective detail: 224 -> 64 -> 224, which is
                # what the model saw during training.
                t = cv2.resize(cv2.resize(t, (64, 64), interpolation=cv2.INTER_AREA),
                               (TILE, TILE), interpolation=cv2.INTER_LINEAR)
            yield t


def classify(model_path, tile_iter, colour):
    from ai_edge_litert.interpreter import Interpreter
    it = Interpreter(model_path=model_path)
    it.allocate_tensors()
    i_in = it.get_input_details()[0]["index"]
    i_out = it.get_output_details()[0]["index"]
    hist = collections.Counter()
    for t in tile_iter:
        a = t if colour == "bgr" else t[:, :, ::-1]   # frame is BGR
        it.set_tensor(i_in, np.ascontiguousarray(a)[None, ...])
        it.invoke()
        hist[int(np.argmax(it.get_tensor(i_out)[0]))] += 1
    return hist


def fmt_hist(h, top=4):
    tot = sum(h.values()) or 1
    parts = [f"{CLASSES[c][:12]}={n*100//tot}%" for c, n in h.most_common(top)]
    return " ".join(parts)


def separation(a, b):
    """Total-variation distance between two class histograms: 0 identical, 1 disjoint."""
    ta, tb = sum(a.values()) or 1, sum(b.values()) or 1
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a[k]/ta - b[k]/tb) for k in keys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DATA)
    ap.add_argument("--model",
                    default="../tf-dipp-module/custom_conv_model_landcover.tflite")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--dump-tiles", type=int, metavar="N")
    ap.add_argument("-o", "--out", default="analysis/stage4.5-models")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    urban = os.path.join(a.data, "amager-no-clouds.bayerRG")
    sea = os.path.join(a.data, "koge-coast-no-clouds.bayerRG")

    if a.dump_tiles:
        for name, p in (("amager", urban), ("koge", sea)):
            f = device_frame(p)
            ts = list(tiles(f))
            print(f"{name}: {len(ts)} tiles of {ts[0].shape}")
            for i in range(0, len(ts), max(1, len(ts)//a.dump_tiles))[:a.dump_tiles]:
                cv2.imwrite(os.path.join(a.out, f"tile_{name}_{i:03d}_deviceBGR.png"), ts[i])
                cv2.imwrite(os.path.join(a.out, f"tile_{name}_{i:03d}_asRGB.png"),
                            ts[i][:, :, ::-1])
            cv2.imwrite(os.path.join(a.out, f"frame_{name}_thumb.png"),
                        cv2.resize(f, (W//6, H//6)))
        print(f"wrote tiles and thumbnails to {a.out}")
        return 0

    if not a.sweep:
        ap.error("pass --sweep or --dump-tiles")

    variants = [(c, r, s)
                for c in ("bgr", "rgb")
                for r in ("minmax", "fixed12")
                for s in ("native", "blur64", "wide")]
    rows = []
    print(f"{'colour':6} {'radiometry':10} {'scale':7} {'sep':>5}  urban / sea")
    print("-" * 96)
    for colour, radio, scale in variants:
        hu = classify(a.model, tiles(device_frame(urban, radiometry=radio), scale), colour)
        hs = classify(a.model, tiles(device_frame(sea, radiometry=radio), scale), colour)
        sep = separation(hu, hs)
        tag = "  <-- device" if (colour, radio, scale) == ("bgr", "minmax", "native") else ""
        print(f"{colour:6} {radio:10} {scale:7} {sep:5.2f}  {fmt_hist(hu,3):<40} | "
              f"{fmt_hist(hs,3)}{tag}")
        rows.append({"colour": colour, "radiometry": radio, "scale": scale,
                     "separation": sep,
                     "urban": {CLASSES[k]: v for k, v in hu.items()},
                     "sea": {CLASSES[k]: v for k, v in hs.items()}})

    dest = os.path.join(a.out, "replica_sweep.json")
    json.dump({"model": a.model, "variants": rows}, open(dest, "w"), indent=2)
    print(f"\nwrote {dest}")
    best = max(rows, key=lambda r: r["separation"])
    print(f"best separation: {best['separation']:.2f} at "
          f"{best['colour']}/{best['radiometry']}/{best['scale']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
