#!/usr/bin/env python3
"""Fetch EuroSAT-MS: the same corpus as EuroSAT RGB, as raw L1C reflectance.

    training/fetch_eurosat_ms.py --out ~/dipp-datasets/eurosat-ms

WHY. The classifiers are trained on EuroSAT's RGB JPEGs, which are a PROCESSED
8-bit product, and deployed on uncorrected top-of-atmosphere imagery. Measured:

    EuroSAT JPEG pixels        mean 126.3 / 118.1 / 115.8   red-dominant
    Sentinel-2 L1C reflectance mean 1228  / 1276  / 1411    blue-dominant

TOA is blue-dominant because Rayleigh scattering inflates blue; EuroSAT's RGB is
not. That is an atmospheric-correction-shaped difference, and no gain/offset
inverts it -- /2000, /2500, /3000 and /4000 were all tried and top out at 43%
coarse-group agreement, with the alpha=1.4 rung stuck near 35% and tree recall
at zero. The models call dark vegetation SeaLake because in the device's
rendering it is dark and blue.

EuroSAT-MS is the SAME 27,000 images and the SAME 10 classes, distributed as
13-band GeoTIFFs of L1C reflectance -- only the RGB rendering is the processed
artifact. So this is not a change of dataset; it is the same dataset without
somebody else's tone mapping, which puts training, validation (CloudSEN12-l1c)
and deployment in one radiometric domain for the first time.

NOT the answer, for the record: BigEarthNet-S2 is L2A, i.e. atmospherically
corrected, so it would reintroduce exactly this mismatch. It solves a different
problem (EuroSAT's saturated ~2-point accuracy range) and should be considered
on those merits, separately.

Filenames match the RGB set (Forest_1.tif <-> Forest_1.jpg), so the existing
train/test split is reused by name and the holdout stays comparable.
"""

import argparse
import os
import subprocess
import sys
import zipfile

# Zenodo first: the DFKI host the paper's repo links has been unreachable
# (connection timeout on 443), and the file there is named EuroSATallBands.zip
# while Zenodo calls the same archive EuroSAT_MS.zip. Guessing the name against
# Zenodo returns a 14 kB HTML error page that looks like a tiny download rather
# than a failure, so the size check below is load-bearing.
# Order matters, and not for availability: both Zenodo URLs serve the same
# archive, but measured back to back the /api/.../content route ran at 83 kB/s
# (6h54m projected) while /records/...?download=1 ran at 4.8 MB/s -- 57x, or
# seven minutes. Same host, same file.
URLS = [
    "https://zenodo.org/records/7711810/files/EuroSAT_MS.zip?download=1",
    "https://zenodo.org/api/records/7711810/files/EuroSAT_MS.zip/content",
    "https://madm.dfki.de/files/sentinel/EuroSATallBands.zip",  # unreachable
]
CLASSES = ["AnnualCrop", "Forest", "HerbaceousVegetation", "Highway",
           "Industrial", "Pasture", "PermanentCrop", "Residential", "River",
           "SeaLake"]
RGB_BANDS = (3, 2, 1)          # B04, B03, B02 in the 13-band order, 0-indexed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/dipp-datasets/eurosat-ms"))
    ap.add_argument("--keep-zip", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    zp = os.path.join(a.out, "EuroSATallBands.zip")

    if not os.path.isdir(os.path.join(a.out, "ds")) and not any(
            os.path.isdir(os.path.join(a.out, c)) for c in CLASSES):
        if not os.path.exists(zp):
            for url in URLS:
                print(f"downloading {url}", flush=True)
                # -C - resumes a partial file rather than restarting 2 GB.
                r = subprocess.run(["curl", "-L", "-C", "-", "-o", zp, url])
                got = os.path.getsize(zp) if os.path.exists(zp) else 0
                if r.returncode == 0 and got > 10 ** 9:
                    break
                # An HTML error page is a successful HTTP fetch of the wrong
                # thing; only the size distinguishes it from the archive.
                print(f"  failed (curl={r.returncode}, {got/2**20:.1f} MB, "
                      f"expected ~1970 MB), trying next mirror", flush=True)
                if os.path.exists(zp):
                    os.remove(zp)
            else:
                sys.exit("all mirrors failed")
        print(f"extracting {os.path.getsize(zp) / 2**30:.2f} GB", flush=True)
        with zipfile.ZipFile(zp) as z:
            z.extractall(a.out)
        if not a.keep_zip:
            os.remove(zp)

    # The archive nests differently between mirrors; find the class dirs.
    root = None
    for dirpath, dirnames, _ in os.walk(a.out):
        if all(c in dirnames for c in CLASSES[:3]):
            root = dirpath
            break
    if root is None:
        sys.exit(f"could not find class directories under {a.out}")
    print(f"class root: {root}")

    import numpy as np
    try:
        import rasterio as rio
    except ImportError:
        sys.exit("rasterio needed to inspect the GeoTIFFs")

    tot = 0
    acc = []
    for c in CLASSES:
        fs = sorted(os.listdir(os.path.join(root, c)))
        tot += len(fs)
        with rio.open(os.path.join(root, c, fs[0])) as src:
            arr = src.read()
        acc.append((c, len(fs), arr.shape))
        if c == "Forest":
            forest = arr
    print(f"\n{tot} images across {len(CLASSES)} classes")
    for c, n, shp in acc:
        print(f"  {c:22} {n:6}  first image {shp}")

    # The check that matters: is this TOA (blue-dominant) like CloudSEN12-l1c,
    # rather than the red-dominant rendering of the RGB JPEGs?
    rgb = forest[list(RGB_BANDS)].astype(np.float32)
    print(f"\nForest sample, bands B04/B03/B02 (should be BLUE-dominant if L1C):")
    print(f"  mean {np.round(rgb.reshape(3, -1).mean(1), 1)}  "
          f"dtype {forest.dtype}")
    print("  EuroSAT RGB JPEG for comparison: 126.3 / 118.1 / 115.8 "
          "(red-dominant, processed)")
    print("  CloudSEN12 L1C for comparison  : 1228 / 1276 / 1411 "
          "(blue-dominant, TOA)")


if __name__ == "__main__":
    main()
