#!/usr/bin/env python3
"""Pack fetched CloudSEN12 samples into memory-mappable shards, and measure them.

    training/pack_cloudsen12.py --out ~/dipp-datasets/cloudsen12 --jobs 48

Two reasons this is a separate step rather than something the training loop
does. Ten thousand individually-compressed files are a bad read pattern for a
training loop, and decompressing them once into a memmap turns every epoch into
sequential I/O. And the reflectance -> uint8 mapping the training needs has to
be derived from the data; the percentiles printed here are that input.

Deliberately NOT done here: the uint8 conversion itself, and any resizing. Both
are modelling decisions. Keeping uint16 at 512px leaves them reversible -- a
device tile is a 224px CROP of a larger frame, not a downscaled scene, so
training must crop, and a guess baked in at pack time could not be undone
without refetching 15.5 GB.

Resumable: a split whose shards already exist is skipped. Delete the .npy pair
to force a repack.
"""

import argparse
import concurrent.futures as cf
import glob
import json
import os

import numpy as np

SIZE = 512
CLASSES = ["clear", "thick cloud", "thin cloud", "cloud shadow"]

# The device delivers uint8 tiles whose measured domain is this, pooled over 100
# real calibrated tiles from four scenes. Matching CloudSEN12 to it is what
# makes a model trained here transfer to the payload, and the percentiles below
# are what that affine gets fitted against.
DEVICE_TARGET_MEAN = [94.4, 104.5, 111.2]
DEVICE_TARGET_STD = [50.4, 34.8, 29.6]


def _chunk(job):
    """Fill a disjoint slice of the shards.

    Module level on purpose: ProcessPoolExecutor pickles the callable, and a
    closure over the loop variables cannot be pickled.
    """
    rgb_path, lab_path, lo, paths = job
    # Reopened per process -- a memmap must not be shared across a fork.
    R = np.load(rgb_path, mmap_mode="r+")
    L = np.load(lab_path, mmap_mode="r+")
    bad = []
    for k, f in enumerate(paths):
        try:
            with np.load(f) as z:
                R[lo + k] = np.transpose(z["rgb"], (1, 2, 0))  # (3,H,W)->(H,W,3)
                L[lo + k] = z["label"]
        except Exception as e:                                  # noqa: BLE001
            # A truncated sample must not kill the whole pack; name it and
            # carry on, leaving that slot zeroed.
            bad.append(f"{os.path.basename(f)}: {type(e).__name__}")
    R.flush()
    L.flush()
    return len(paths), bad


def pack(out, split, jobs):
    files = sorted(glob.glob(os.path.join(out, split, "*.npz")))
    if not files:
        print(f"  {split:11} nothing fetched, skipping")
        return
    rgb_path = os.path.join(out, f"{split}_rgb.npy")
    lab_path = os.path.join(out, f"{split}_label.npy")
    if os.path.exists(rgb_path) and os.path.exists(lab_path):
        print(f"  {split:11} shards exist, skipping ({len(files)} files)")
        return

    n = len(files)
    gb = n * SIZE * SIZE * 3 * 2 / 2**30
    print(f"  {split:11} packing {n} samples (~{gb:.1f} GB rgb) with {jobs} procs",
          flush=True)
    # Allocate first, then let workers fill disjoint slices.
    np.lib.format.open_memmap(rgb_path, mode="w+", dtype=np.uint16,
                              shape=(n, SIZE, SIZE, 3)).flush()
    np.lib.format.open_memmap(lab_path, mode="w+", dtype=np.uint8,
                              shape=(n, SIZE, SIZE)).flush()

    nj = max(1, min(jobs, n))
    edges = np.linspace(0, n, nj + 1).astype(int)
    work = [(rgb_path, lab_path, int(a), files[int(a):int(b)])
            for a, b in zip(edges[:-1], edges[1:]) if b > a]

    done, bad = 0, []
    with cf.ProcessPoolExecutor(max_workers=nj) as ex:
        for got, errs in ex.map(_chunk, work):
            done += got
            bad += errs
            print(f"    {done}/{n}", flush=True)
    if bad:
        print(f"  ⚠ {len(bad)} unreadable samples, slots left zeroed:")
        for b in bad[:10]:
            print(f"      {b}")
    print(f"  {split:11} -> {rgb_path}", flush=True)


def measure(out):
    rgb_path = os.path.join(out, "train_rgb.npy")
    if not os.path.exists(rgb_path):
        print("no train shard to measure")
        return
    R = np.load(rgb_path, mmap_mode="r")
    idx = np.linspace(0, len(R) - 1, min(400, len(R))).astype(int)
    take = np.asarray(R[idx]).reshape(-1, 3).astype(np.float32)

    st = {"n_scenes_sampled": int(len(idx)),
          "device_target_mean": DEVICE_TARGET_MEAN,
          "device_target_std": DEVICE_TARGET_STD,
          "mean": take.mean(0).tolist(),
          "std": take.std(0).tolist()}
    for q in (1, 2, 50, 98, 99):
        st[f"p{q}"] = np.percentile(take, q, axis=0).tolist()

    # A first-cut affine per band, mapping reflectance onto the device domain by
    # matching mean and standard deviation. It is a STARTING POINT, not a
    # conclusion: matching the first two moments does not guarantee clouds land
    # where clouds landed on the device, and that is the thing that has to be
    # right. The training script should be free to refit it.
    gain = [DEVICE_TARGET_STD[c] / st["std"][c] if st["std"][c] else 0.0
            for c in range(3)]
    off = [DEVICE_TARGET_MEAN[c] - gain[c] * st["mean"][c] for c in range(3)]
    st["suggested_gain"] = gain
    st["suggested_offset"] = off

    json.dump(st, open(os.path.join(out, "stats.json"), "w"), indent=1)
    print("\nreflectance stats (uint16), per band R,G,B:")
    for k in ("p1", "p2", "mean", "p50", "p98", "p99", "std"):
        print(f"  {k:6} {[round(v, 1) for v in st[k]]}")
    print(f"  device target   mean {DEVICE_TARGET_MEAN} std {DEVICE_TARGET_STD}")
    print(f"  suggested gain  {[round(g, 6) for g in gain]}")
    print(f"  suggested offset{[round(o, 2) for o in off]}")

    lab_path = os.path.join(out, "train_label.npy")
    if os.path.exists(lab_path):
        L = np.load(lab_path, mmap_mode="r")
        cnt = np.bincount(np.asarray(L[idx]).ravel(), minlength=4).astype(float)
        print("\nlabel mix (train sample):")
        for i, nm in enumerate(CLASSES):
            print(f"  {i} {nm:13} {100 * cnt[i] / cnt.sum():5.2f}%")
        # Thin cloud and shadow are the classes that need context rather than
        # brightness -- the reason this dataset can give model capacity
        # something to buy, where 38-Cloud could not.
        ctx = 100 * (cnt[2] + cnt[3]) / cnt.sum()
        print(f"  thin cloud + shadow = {ctx:.2f}% of pixels "
              f"(the part a bigger model can actually earn)")
    print(f"\nwrote {os.path.join(out, 'stats.json')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/dipp-datasets/cloudsen12"))
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 8)
    a = ap.parse_args()
    print(f"packing {a.out} with {a.jobs} processes")
    for split in ("train", "validation", "test"):
        pack(a.out, split, a.jobs)
    measure(a.out)


if __name__ == "__main__":
    main()
