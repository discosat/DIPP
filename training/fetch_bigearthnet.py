#!/usr/bin/env python3
"""Stream BigEarthNet (HDF5 export) into a compact local RGB + 19-label set.

    training/fetch_bigearthnet.py --splits train val --out ~/dipp-datasets/bigearthnet

Why this dataset. EuroSAT is saturated: alpha=0.35 at 96px already reaches
96.0% quantised and the ceiling is ~98%, so the entire usable range is about
two points and no ladder can be spread across it. BigEarthNet is *multi-label*
-- 3.55 labels per patch on average, up to 9, over 19 classes whose frequencies
run from 0.2% to 53.8% -- so performance degrades gracefully with capacity
instead of clipping. It is also Sentinel-2, matching the payload's sensor.

Why stream. The archive is ~110 GB for train+val and the parts are
**geographically grouped** -- each holds only 10-29 distinct Sentinel-2 tiles
and neighbouring parts share almost none -- so taking a few parts would sample
a handful of regions rather than the distribution. Every part is therefore
fetched, reduced, and deleted, which keeps peak disk at roughly one part
(~15 GB) instead of 110 GB.

What is kept. Bands 3,2,1 (B04/B03/B02 = RGB) as **uint16 reflectance**, plus
labels19. Raw reflectance rather than uint8 for the same reason as CloudSEN12:
the mapping to 8-bit is a modelling decision and belongs in the training
script, not baked into the archive. Storing 12 float32 bands would need ~350 GB
and buys nothing for an RGB payload.

Resumable: a part whose output already exists is skipped.
"""

import argparse
import os
import subprocess
import sys
import time

import h5py
import numpy as np

BASE = ("https://huggingface.co/datasets/lc-col/bigearthnet/resolve/main/"
        "bigearthnet_{split}_p{n}.hdf5.gz")
PARTS = {"train": 18, "val": 9, "test": 9}
# h5py fancy indexing must be given in increasing order, so read
# B02,B03,B04 and reverse to R,G,B. Asking for [3,2,1] directly raises
# TypeError: Indexing elements must be in increasing order -- on every part.
RGB_ASC = [1, 2, 3]      # B02, B03, B04 in BigEarthNet's 12-band order


def run(cmd, **kw):
    return subprocess.run(cmd, shell=True, check=True, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/dipp-datasets/bigearthnet"))
    ap.add_argument("--splits", nargs="+", default=["train", "val"])
    ap.add_argument("--tmp", default="/tmp/ben-stream")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.tmp, exist_ok=True)
    t0 = time.time()

    for split in a.splits:
        for n in range(PARTS[split]):
            dst = os.path.join(a.out, f"{split}_p{n}.npz")
            if os.path.exists(dst):
                print(f"  {split} p{n}: already present, skipping", flush=True)
                continue
            gz = os.path.join(a.tmp, f"{split}_p{n}.hdf5.gz")
            h5 = gz[:-3]
            url = BASE.format(split=split, n=n)
            try:
                t = time.time()
                run(f"curl -sSL --retry 3 --retry-delay 5 '{url}' -o '{gz}'")
                run(f"gunzip -f '{gz}'")
                with h5py.File(h5, "r") as f:
                    rgb = f["images"][:, RGB_ASC][:, ::-1].astype(np.uint16)
                    lab = f["labels19"][:].astype(np.uint8)
                tmp_out = dst + ".part"
                np.savez_compressed(tmp_out, rgb=rgb, label=lab)
                os.replace(tmp_out + ".npz" if os.path.exists(tmp_out + ".npz")
                           else tmp_out, dst)
                mb = os.path.getsize(dst) / 1e6
                print(f"  {split} p{n}: {len(lab)} samples -> {mb:.0f} MB "
                      f"in {time.time()-t:.0f}s", flush=True)
            except Exception as e:                       # noqa: BLE001
                print(f"  {split} p{n}: FAILED {type(e).__name__}: {e}",
                      file=sys.stderr, flush=True)
            finally:
                for p in (gz, h5):
                    if os.path.exists(p):
                        os.remove(p)

    print(f"\ndone in {(time.time()-t0)/60:.1f} min -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
