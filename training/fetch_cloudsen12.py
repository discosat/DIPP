#!/usr/bin/env python3
"""Materialise the expert-labelled slice of CloudSEN12+ L1C locally.

    training/fetch_cloudsen12.py --out ~/dipp-datasets/cloudsen12

Why this dataset. 38-Cloud made the cloud rungs useless: a 3,825-parameter
model reached IoU 0.741 and a 2,207,137-parameter model only 0.865, because
binary thick-cloud detection is close to a brightness threshold and capacity
has nothing to buy. CloudSEN12 labels four classes -- clear, thick cloud,
**thin cloud**, cloud shadow -- and the two extra ones are exactly the cases
that need context rather than brightness. It is also Sentinel-2, where
38-Cloud is Landsat-8, so it removes a sensor mismatch against the imagery the
payload actually sees.

Why L1C and not L2A. Level-1C is top-of-atmosphere: what a satellite has before
any ground-side atmospheric correction. That is the payload's situation.

What is fetched. Only `label_type == "high"` at the 509px (padded to 512)
size: 10,000 samples, 15.5 GB, already split train/val/test (8,490/535/975)
over 2,000 distinct ROIs. The scribble and unlabelled majority are skipped, as
are the 847 2000px scenes.

Stored as raw uint16 reflectance rather than uint8. The device delivers uint8,
but the mapping from reflectance to uint8 is a modelling decision that belongs
in the training script, and baking a guess in here would be unrecoverable.
512x512 is likewise kept whole: a device tile is a 224px *crop* of a larger
frame, not a downscaled scene, so cropping at training time matches the domain
where resizing would not.

Resumable: a sample whose output file already exists is skipped.
"""

import argparse
import concurrent.futures as cf
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
import numpy as np                                            # noqa: E402
import rasterio as rio                                        # noqa: E402
import tacoreader                                             # noqa: E402

SIZE = 512
RGB_BANDS = [4, 3, 2]          # Sentinel-2 B4/B3/B2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/dipp-datasets/cloudsen12"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--mode", default="cloudsen12-l1c")
    # Each sample is an independent HTTP range read, so this is I/O bound, not
    # CPU bound: serial fetching runs at ~15/min, i.e. 11 hours for the set.
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()

    ds = tacoreader.load(f"tacofoundation:{a.mode}")
    keep = [i for i, (lt, sh) in enumerate(zip(ds["label_type"], ds["real_proj_shape"]))
            if lt == "high" and sh == 509]
    splits = list(ds["tortilla:data_split"])
    ids = list(ds["tortilla:id"])
    if a.limit:
        keep = keep[:a.limit]
    print(f"{len(keep)} high-quality 509px samples to fetch into {a.out}")

    for s in ("train", "validation", "test"):
        os.makedirs(os.path.join(a.out, s), exist_ok=True)

    def fetch(i):
        dst = os.path.join(a.out, splits[i], f"{ids[i]}.npz")
        if os.path.exists(dst):
            return "skip", None
        try:
            s = ds.read(i)
            with rio.open(s.read(0)) as src:
                rgb = src.read(RGB_BANDS,
                               window=rio.windows.Window(0, 0, SIZE, SIZE))
            with rio.open(s.read(1)) as lab:
                y = lab.read(1, window=rio.windows.Window(0, 0, SIZE, SIZE))
            tmp = dst + ".part"
            np.savez_compressed(tmp, rgb=rgb.astype(np.uint16),
                                label=y.astype(np.uint8))
            # savez_compressed appends .npz to a name without one; rename the
            # file it actually wrote, so a kill never leaves a truncated .npz
            # that the resume check would treat as complete.
            os.replace(tmp + ".npz" if os.path.exists(tmp + ".npz") else tmp, dst)
            return "ok", None
        except Exception as e:                    # noqa: BLE001
            return "fail", f"{ids[i]}: {type(e).__name__}: {e}"

    t0, done, skipped, failed = time.time(), 0, 0, 0
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for n, (status, err) in enumerate(ex.map(fetch, keep), 1):
            if status == "ok":
                done += 1
            elif status == "skip":
                skipped += 1
            else:
                failed += 1
                if failed <= 20:
                    print(f"  {err}", file=sys.stderr)
            if n % 200 == 0:
                el = time.time() - t0
                rate = done / el if el else 0
                left = (len(keep) - n) / rate if rate else 0
                print(f"  {n}/{len(keep)}  fetched={done} skipped={skipped} "
                      f"failed={failed}  {rate:.1f}/s  eta {left/60:.0f} min",
                      flush=True)

    print(f"\ndone: fetched={done} skipped={skipped} failed={failed} "
          f"in {(time.time()-t0)/60:.1f} min")
    return 1 if failed and not done else 0


if __name__ == "__main__":
    sys.exit(main())
