#!/usr/bin/env python3
"""Fetch ESA WorldCover land cover for the CloudSEN12 TEST split.

    training/fetch_cloudsen12_extra.py --out ~/dipp-datasets/cloudsen12

Why only land cover, and only test. This is not training data -- it completes a
VALIDATION corpus. After this the test split has, on the same Sentinel-2 pixels:

    imagery        already fetched (cloudsen12-l1c)
    cloud labels   already fetched, expert-reviewed
    land cover     this script (cloudsen12-extra, LC10)

which is the first thing that can validate the pipeline END TO END in the
deployment domain: did the classifier keep the tiles it should have, and did the
segmenter mask the pixels it should have. Today neither has per-pixel
device-domain ground truth, so segmentation rungs are ranked on an assumption --
that `koge-coast-no-clouds` contains no cloud at all. Part of the 19.86%
"false" masking the best rung shows there may be real haze the eye misses, and
without labels that cannot be separated from over-prediction.

⚠️ Land cover here is ESA WorldCover, a MODEL PRODUCT at 10 m, not expert
labels. It is adequate for asking "is this tile built-up or water" and not
adequate as a quality benchmark. The cloud labels in the main split ARE expert;
do not conflate their authority.

Its 11 classes also do not map onto EuroSAT's 10 -- the paper's class_index 7
(Residential) corresponds to WorldCover 50 (built-up). Any comparison has to
state that mapping rather than imply the label sets are the same.

Asset index 1 is LC10, per the upstream demo notebook:
    'elevation': read(0), 'lc10': read(1), 'vv': read(2), 'vh': read(3), ...
"""

import argparse
import concurrent.futures as cf
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
import numpy as np                                              # noqa: E402
import rasterio as rio                                          # noqa: E402

# tacoreader 2.0 moved the v1 "tacofoundation:" format behind a legacy import.
try:
    import tacoreader.v1 as tacoreader                          # noqa: E402
    _API = "v1-legacy"
except ImportError:
    import tacoreader                                           # noqa: E402
    _API = "native"

SIZE = 512
LC10_ASSET = 1

# ESA WorldCover v100 class codes, for whoever reads the shards next.
WORLDCOVER = {10: "tree cover", 20: "shrubland", 30: "grassland",
              40: "cropland", 50: "built-up", 60: "bare/sparse",
              70: "snow and ice", 80: "permanent water",
              90: "herbaceous wetland", 95: "mangroves",
              100: "moss and lichen"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/dipp-datasets/cloudsen12"))
    ap.add_argument("--split", default="test",
                    help="which CloudSEN12 split to complete (default test)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--asset", type=int, default=LC10_ASSET)
    a = ap.parse_args()

    print(f"tacoreader API: {_API}", flush=True)
    ds = tacoreader.load("tacofoundation:cloudsen12-extra")
    ids = list(ds["tortilla:id"])
    # `tortilla:data_split` is entirely None in the extra mode -- the column
    # exists but carries no values -- so the split cannot be selected there.
    # Match on tortilla:id against what the main fetch already wrote instead,
    # which also guarantees every land-cover tile has imagery and an expert
    # cloud mask beside it. Filtering by split returned 0 rows and would have
    # looked like a missing dataset.
    want_dir = os.path.join(a.out, a.split)
    if not os.path.isdir(want_dir):
        sys.exit(f"{want_dir} not found; fetch the l1c split first")
    want = {os.path.basename(f)[:-4] for f in os.listdir(want_dir)
            if f.endswith(".npz")}
    if not want:
        sys.exit(f"no samples in {want_dir}")
    print(f"matching against {len(want)} ids already fetched in {a.split}/",
          flush=True)
    keep = [i for i, (lt, sh) in enumerate(zip(ds["label_type"],
                                               ds["real_proj_shape"]))
            if lt == "high" and sh == 509 and ids[i] in want]
    if a.limit:
        keep = keep[:a.limit]
    dest = os.path.join(a.out, f"{a.split}_lc")
    os.makedirs(dest, exist_ok=True)
    print(f"{len(keep)} samples in split '{a.split}' -> {dest}", flush=True)
    if not keep:
        sys.exit("nothing matched; check --split against the l1c fetch")

    def one(i):
        dst = os.path.join(dest, f"{ids[i]}.npz")
        if os.path.exists(dst):
            return "skip", None
        # Same throttling behaviour as the main fetch: the host rate-limits
        # sustained parallel range reads, so back off rather than give up.
        for attempt in range(4):
            try:
                s = ds.read(i)
                with rio.open(s.read(a.asset)) as src:
                    lc = src.read(1, window=rio.windows.Window(0, 0, SIZE, SIZE))
                tmp = dst + ".part"
                np.savez_compressed(tmp, lc=lc.astype(np.uint8))
                os.replace(tmp + ".npz" if os.path.exists(tmp + ".npz") else tmp,
                           dst)
                return "ok", None
            except Exception as e:                               # noqa: BLE001
                if attempt == 3:
                    return "fail", f"{ids[i]}: {type(e).__name__}: {e}"
                time.sleep(2 ** attempt * 3)

    t0, done, skipped, failed = time.time(), 0, 0, 0
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for n, (st, err) in enumerate(ex.map(one, keep), 1):
            done += st == "ok"; skipped += st == "skip"; failed += st == "fail"
            if st == "fail" and failed <= 10:
                print(f"  {err}", file=sys.stderr)
            if n % 100 == 0:
                print(f"  {n}/{len(keep)} ok={done} skip={skipped} fail={failed}",
                      flush=True)
    print(f"done: ok={done} skip={skipped} fail={failed} "
          f"in {(time.time()-t0)/60:.1f} min")

    # Report the class mix, so the next reader knows what is actually in here
    # rather than trusting the class table above.
    import glob
    fs = sorted(glob.glob(os.path.join(dest, "*.npz")))[:200]
    if fs:
        cnt = np.zeros(256, np.int64)
        for f in fs:
            with np.load(f) as z:
                cnt += np.bincount(z["lc"].ravel(), minlength=256)
        tot = cnt.sum()
        print(f"\nland-cover mix over {len(fs)} scenes:")
        for code in np.argsort(-cnt)[:8]:
            if cnt[code]:
                print(f"  {code:3} {WORLDCOVER.get(int(code), '?'):20} "
                      f"{100*cnt[code]/tot:5.2f}%")
    return 1 if failed and not done else 0


if __name__ == "__main__":
    sys.exit(main())
