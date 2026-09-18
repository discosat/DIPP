#!/usr/bin/env python3
"""Fit the CloudSEN12 -> device radiometric map from CONTENT-MATCHED anchors.

    training/cs12_domain_fit.py --out ~/dipp-datasets/cloudsen12

Why not just match mean and standard deviation. That is what pack_cloudsen12.py
suggests as a starting point, and it is wrong here: CloudSEN12 is ~45% cloud
while the device statistics were pooled over largely clear scenes, so matching
the moments of two differently-composed corpora aligns the corpora, not the
physics. With that fit the 98th percentile of reflectance lands at 278 and
clips -- and the clipped pixels are cloud, the thing the task depends on.

What this does instead. The device has been measured on real frames of BOTH
kinds, which gives two anchors per band:

    clear ground : mean  62.2 /  78.5 /  83.7   (two cloud-free scenes)
    cloud        : mean 168.5 / 145.8 / 146.6   (one fully-clouded scene)

CloudSEN12 has labels, so the same two quantities can be computed on it. Two
anchors per band is exactly enough to solve for a per-band gain and offset, and
the result maps clear ground onto clear ground and cloud onto cloud rather than
making the histograms agree on average.

It also reports the CEILING. The device's calibration tops out at 182/165/164 --
it cannot produce a brighter pixel -- so training data clipped at 255 would show
the model values the hardware never emits.
"""

import argparse
import json
import os

import numpy as np

# Measured on the board's own frames via analysis/device_labelled_eval.py.
DEV_CLEAR = np.array([62.2, 78.5, 83.7], np.float64)     # two cloud-free scenes
DEV_CLOUD = np.array([168.5, 145.8, 146.6], np.float64)  # koge-coast-full-clouds
DEV_MAX = np.array([182.0, 165.0, 164.0], np.float64)    # calibration ceiling

CLEAR, THICK, THIN, SHADOW = 0, 1, 2, 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/dipp-datasets/cloudsen12"))
    ap.add_argument("--scenes", type=int, default=300, help="scenes to sample")
    ap.add_argument("--thin-is-cloud", action="store_true", default=True)
    a = ap.parse_args()

    R = np.load(os.path.join(a.out, "train_rgb.npy"), mmap_mode="r")
    L = np.load(os.path.join(a.out, "train_label.npy"), mmap_mode="r")
    idx = np.linspace(0, len(R) - 1, min(a.scenes, len(R))).astype(int)

    # Padding is zeros on the left and bottom of every 512px scene. Including it
    # drags every statistic toward zero -- it is why pack_cloudsen12.py reports
    # p1 = [0,0,0] -- so it is excluded here by mask, not by assuming a side.
    sums = {k: np.zeros(3) for k in (CLEAR, THICK, THIN, SHADOW)}
    cnts = {k: 0 for k in sums}
    hi = []
    for i in idx:
        rgb = np.asarray(R[i]).astype(np.float64)
        lab = np.asarray(L[i])
        valid = rgb.any(axis=2)
        for k in sums:
            m = (lab == k) & valid
            c = int(m.sum())
            if c:
                sums[k] += rgb[m].sum(axis=0)
                cnts[k] += c
        hi.append(np.percentile(rgb[valid], 99.5, axis=0))

    means = {k: (sums[k] / cnts[k] if cnts[k] else np.full(3, np.nan)) for k in sums}
    names = {CLEAR: "clear", THICK: "thick cloud", THIN: "thin cloud",
             SHADOW: "cloud shadow"}
    tot = sum(cnts.values())
    print(f"class-conditional reflectance means over {len(idx)} scenes "
          f"(padding excluded):")
    for k in (CLEAR, THICK, THIN, SHADOW):
        print(f"  {k} {names[k]:13} {np.round(means[k],1)}  "
              f"{100*cnts[k]/tot:5.2f}% of valid px")

    # Cloud anchor: whichever classes we intend to call cloud. Thin cloud is
    # dimmer than thick, so including it moves the anchor down, and the fit has
    # to reflect the label collapse the training will actually use.
    if a.thin_is_cloud and cnts[THIN]:
        cloud_src = (sums[THICK] + sums[THIN]) / (cnts[THICK] + cnts[THIN])
        label = "thick+thin"
    else:
        cloud_src = means[THICK]
        label = "thick only"
    clear_src = means[CLEAR]

    # Two anchors, two unknowns, per band.
    gain = (DEV_CLOUD - DEV_CLEAR) / (cloud_src - clear_src)
    off = DEV_CLEAR - gain * clear_src

    print(f"\nanchors ({label} as cloud):")
    print(f"  cloudsen12 clear {np.round(clear_src,1)} -> device {DEV_CLEAR}")
    print(f"  cloudsen12 cloud {np.round(cloud_src,1)} -> device {DEV_CLOUD}")
    print(f"\nFITTED  gain   = {np.round(gain,6).tolist()}")
    print(f"FITTED  offset = {np.round(off,2).tolist()}")

    p995 = np.mean(hi, axis=0)
    mapped = p995 * gain + off
    print(f"\nsanity: reflectance p99.5 {np.round(p995,0)} maps to "
          f"{np.round(mapped,0)} against a device ceiling of {DEV_MAX}")
    if (mapped > DEV_MAX + 10).any():
        print("  ⚠ bright pixels exceed what the device can emit; they will be")
        print("    clipped to the ceiling, which is correct but means the very")
        print("    brightest cloud is compressed. Acceptable: the device does")
        print("    the same thing to the same scenes.")

    json.dump({"gain": gain.tolist(), "offset": off.tolist(),
               "device_clear": DEV_CLEAR.tolist(),
               "device_cloud": DEV_CLOUD.tolist(),
               "device_max": DEV_MAX.tolist(),
               "cs12_clear": clear_src.tolist(),
               "cs12_cloud": cloud_src.tolist(),
               "cloud_definition": label,
               "class_means": {names[k]: means[k].tolist() for k in names},
               "class_fraction": {names[k]: cnts[k] / tot for k in names}},
              open(os.path.join(a.out, "domain_fit.json"), "w"), indent=1)
    print(f"\nwrote {os.path.join(a.out, 'domain_fit.json')}")


if __name__ == "__main__":
    main()
