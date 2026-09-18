#!/usr/bin/env python3
"""Pack EuroSAT-MS into one array, with the existing train/test split by name.

    training/pack_eurosat_ms.py

27,000 GeoTIFFs of 13-band L1C reflectance -> one .npz holding B04/B03/B02 as
uint16 (27000, 64, 64, 3), labels, class names, and a split flag matched by
filename against the RGB corpus already used for the locked ladder
(EuroSat-working/{training,testing}/<class>/<name>.jpg <-> <name>.tif). The
holdout therefore stays byte-identical to the one behind 75.67 / 89.98 / 98.17.

Kept as raw reflectance: the rendering is the pipeline's frozen gain, applied
at training time, so it can never drift from what the board does.
"""
import os, sys, glob
import numpy as np
import rasterio as rio

MS = os.path.expanduser("~/dipp-datasets/eurosat-ms/EuroSAT_MS")
RGB = os.path.expanduser("~/dipp1-experiments/tensorflow-conversion/EuroSat-working")
OUT = os.path.expanduser("~/dipp-datasets/eurosat-ms/eurosat_ms.npz")
CLASSES = ["AnnualCrop", "Forest", "HerbaceousVegetation", "Highway",
           "Industrial", "Pasture", "PermanentCrop", "Residential", "River",
           "SeaLake"]
RGB_BANDS = (3, 2, 1)   # B04, B03, B02 in the 13-band order

split_of = {}
for sp in ("training", "testing"):
    for c in CLASSES:
        for j in glob.glob(os.path.join(RGB, sp, c, "*.jpg")):
            split_of[(c, os.path.basename(j)[:-4])] = sp

X, y, split, names = [], [], [], []
for ci, c in enumerate(CLASSES):
    fs = sorted(glob.glob(os.path.join(MS, c, "*.tif")))
    for f in fs:
        stem = os.path.basename(f)[:-4]
        sp = split_of.get((c, stem))
        if sp is None:
            continue                       # not in the RGB corpus; skip
        with rio.open(f) as src:
            arr = src.read(list(b + 1 for b in RGB_BANDS))   # rasterio is 1-based
        X.append(np.transpose(arr, (1, 2, 0)).astype(np.uint16))
        y.append(ci); split.append(sp == "testing"); names.append(f"{c}_{stem}")
    print(f"  {c:22} {len(fs):5} tifs", flush=True)

X = np.stack(X); y = np.array(y, np.int16); split = np.array(split, bool)
np.savez(OUT, X=X, y=y, is_test=split, classes=np.array(CLASSES),
         names=np.array(names))
print(f"\n{OUT}: X {X.shape} {X.dtype}  train {int((~split).sum())}  "
      f"test {int(split.sum())}")
fx = X[y == 1][:200].reshape(-1, 3).astype(np.float32)
print(f"Forest B04/B03/B02 mean {np.round(fx.mean(0), 0)}  (blue-dominant = L1C)")
