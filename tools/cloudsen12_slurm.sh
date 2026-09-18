#!/bin/bash
#SBATCH --job-name=cloudsen12
#SBATCH --output=cloudsen12_%j.log
#SBATCH --time=24:00:00
#SBATCH --cpus-per-task=48
#SBATCH --mem=64G
# ^ ADJUST partition/account for your cluster, e.g.
#   #SBATCH --partition=cpu
#   #SBATCH --account=<your-account>
#
# Fetch and pack CloudSEN12+ L1C. CPU-only and GPU-free by design: the fetch is
# thousands of independent HTTP range reads (I/O bound) and the packing is
# decompression, so this is exactly the work to do on a CPU node while a GPU is
# unavailable. Output lands on network storage for the GPU job to read later.
#
# WHY THIS DATASET. The cloud segmenter is trained on 38-Cloud (Landsat-8) and
# deployed on Sentinel-2. Measured on the board, on a frame named full-clouds,
# it marks 0.54% of pixels as cloud and marks near-identical amounts in all 99
# tiles -- it is not tracking content. CloudSEN12 is Sentinel-2 L1C
# (top-of-atmosphere, the payload's actual situation), which removes the sensor
# mismatch. It also has thin cloud and cloud shadow classes, and those are what
# give model capacity something to buy: on 38-Cloud a 3,825-parameter model
# reaches IoU 0.741 and a 2,207,137-parameter model only 0.865, which is why
# the segmentation effort rungs are worth almost nothing today.
#
# USAGE
#   sbatch tools/cloudsen12_slurm.sh              # all stages
#   STAGE=fetch bash tools/cloudsen12_slurm.sh    # login node, if compute
#                                                 # nodes have no internet
#   STAGE=pack  sbatch tools/cloudsen12_slurm.sh  # then pack in the batch job
#
# Resumable at every stage: an already-fetched sample is skipped, and packing
# rewrites shards only if missing. Safe to re-submit after a timeout.
set -u

OUT="${OUT:-$HOME/dipp-datasets/cloudsen12}"      # network storage
VENV="${VENV:-$HOME/.venv-cloudsen12}"
WORKERS="${WORKERS:-6}"      # FETCH threads. Deliberately low and NOT tied to
                             # core count: this is throttled server-side, not
                             # CPU bound. A previous run at higher parallelism
                             # fetched ~800 samples then failed 9,180 in a row,
                             # and the same code succeeded 40/40 straight after.
PACKJOBS="${PACKJOBS:-$(nproc)}"   # PACK processes: this half IS CPU bound
LIMIT="${LIMIT:-}"           # e.g. LIMIT=200 for a smoke run
STAGE="${STAGE:-all}"

echo "=== cloudsen12: stage=$STAGE out=$OUT fetch_threads=$WORKERS pack_procs=$PACKJOBS ==="
mkdir -p "$OUT" || exit 1

# ---------------------------------------------------------------- environment
# Gate on the IMPORTS, not on the interpreter existing. An earlier version
# checked `[ ! -x $VENV/bin/python ]`, so a venv created by a run that then died
# during pip left an executable with no packages -- and every later run skipped
# the install and failed with "No module named numpy". Existence of a venv is
# not evidence that it is usable.
NEED=0
if [ ! -x "$VENV/bin/python" ]; then
  echo "--- creating venv at $VENV ---"
  python3 -m venv "$VENV" || exit 1
  NEED=1
fi
PY="$VENV/bin/python"
"$PY" -c "import numpy, rasterio, tacoreader" 2>/dev/null || NEED=1

if [ "$NEED" = "1" ]; then
  echo "--- installing deps into $VENV ---"
  # Some clusters ship venv without pip bootstrapped.
  "$PY" -m ensurepip --upgrade 2>/dev/null || true
  "$PY" -m pip install --upgrade pip || {
      echo "FATAL: no working pip in $VENV."
      echo "  If ensurepip is disabled here, create the venv with system"
      echo "  site-packages instead:  python3 -m venv --system-site-packages $VENV"
      exit 1; }
  # Not --quiet: a silent failure here is what produced the confusing
  # "imports broken" further down.
  "$PY" -m pip install numpy rasterio tacoreader || {
      echo "FATAL: pip install failed -- see the error above."
      echo "  If this node has no outbound network, build the venv once on a"
      echo "  node that does and point VENV at it (network storage means it"
      echo "  only has to be built once):  VENV=$VENV sbatch \$0"
      exit 1; }
fi

"$PY" -c "import numpy, rasterio, tacoreader" || {
    echo "FATAL: imports still broken after install. Remove $VENV and re-run."
    exit 1; }
echo "  env ok: $("$PY" -c 'import rasterio,tacoreader;print("rasterio",rasterio.__version__)')"
[ "$STAGE" = "none" ] && { echo "env only, stopping"; exit 0; }

# --------------------------------------------------------------------- fetch
cat > "$OUT/_fetch.py" <<'PYEOF'
"""Materialise the expert-labelled 509px slice of CloudSEN12+ L1C.

Only label_type == "high" at 509px (padded to 512): ~10,000 samples, 15.5 GB,
already split train/val/test. Stored as raw uint16 reflectance, NOT uint8: the
mapping from reflectance to the device's uint8 domain is a modelling decision
that belongs in the training script, and baking a guess in here would be
unrecoverable. 512px is kept whole because a device tile is a 224px CROP of a
larger frame, not a downscaled scene, so cropping at training time matches the
deployment domain where resizing would not.
"""
import argparse, concurrent.futures as cf, os, sys, time, warnings
warnings.filterwarnings("ignore")
import numpy as np
import rasterio as rio
import tacoreader

SIZE, RGB_BANDS = 512, [4, 3, 2]        # Sentinel-2 B4/B3/B2

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--limit", type=int)
ap.add_argument("--workers", type=int, default=6)
a = ap.parse_args()

ds = tacoreader.load("tacofoundation:cloudsen12-l1c")
keep = [i for i, (lt, sh) in enumerate(zip(ds["label_type"], ds["real_proj_shape"]))
        if lt == "high" and sh == 509]
splits, ids = list(ds["tortilla:data_split"]), list(ds["tortilla:id"])
if a.limit:
    keep = keep[:a.limit]
print(f"{len(keep)} high-quality 509px samples -> {a.out}", flush=True)
for s in ("train", "validation", "test"):
    os.makedirs(os.path.join(a.out, s), exist_ok=True)

def _one(i, dst):
    s = ds.read(i)
    with rio.open(s.read(0)) as src:
        rgb = src.read(RGB_BANDS, window=rio.windows.Window(0, 0, SIZE, SIZE))
    with rio.open(s.read(1)) as lab:
        y = lab.read(1, window=rio.windows.Window(0, 0, SIZE, SIZE))
    tmp = dst + ".part"
    np.savez_compressed(tmp, rgb=rgb.astype(np.uint16), label=y.astype(np.uint8))
    # savez_compressed appends .npz to a name lacking one; rename whichever it
    # wrote so a kill never leaves a truncated file the resume check trusts.
    os.replace(tmp + ".npz" if os.path.exists(tmp + ".npz") else tmp, dst)

def fetch(i):
    dst = os.path.join(a.out, splits[i], f"{ids[i]}.npz")
    if os.path.exists(dst):
        return "skip", None
    # The host rate-limits sustained parallel range reads: one run fetched ~800
    # then failed 9,180 consecutively, and the same code succeeded 40/40 right
    # afterwards. That is throttling, not bad samples, so it needs backoff.
    for attempt in range(4):
        try:
            _one(i, dst); return "ok", None
        except Exception as e:                                   # noqa: BLE001
            if attempt == 3:
                return "fail", f"{ids[i]}: {type(e).__name__}: {e}"
            time.sleep(2 ** attempt * 3)

t0 = done = skipped = failed = 0; t0 = time.time()
with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
    for n, (st, err) in enumerate(ex.map(fetch, keep), 1):
        done += st == "ok"; skipped += st == "skip"; failed += st == "fail"
        if st == "fail" and failed <= 20:
            print(f"  {err}", file=sys.stderr)
        if n % 200 == 0:
            el = time.time() - t0; rate = done / el if el else 0
            print(f"  {n}/{len(keep)} ok={done} skip={skipped} fail={failed} "
                  f"{rate:.1f}/s eta {(len(keep)-n)/rate/60 if rate else 0:.0f} min",
                  flush=True)
print(f"done: ok={done} skip={skipped} fail={failed} "
      f"in {(time.time()-t0)/60:.1f} min", flush=True)
sys.exit(1 if failed and not done else 0)
PYEOF

if [ "$STAGE" = "all" ] || [ "$STAGE" = "fetch" ]; then
  echo "=== fetch (I/O bound; resumable) ==="
  $PY "$OUT/_fetch.py" --out "$OUT" --workers "$WORKERS" ${LIMIT:+--limit $LIMIT} \
    || echo "WARNING: fetch reported failures; packing whatever arrived"
fi

# ---------------------------------------------------------------------- pack
cat > "$OUT/_pack.py" <<'PYEOF'
"""Pack ~10,000 small .npz into memory-mappable shards, and measure the domain.

Two reasons this is worth a CPU job rather than doing it GPU-side. Ten thousand
individually-compressed files are a terrible read pattern for a training loop,
and decompressing them once into a memmap turns every epoch into sequential
I/O. And the reflectance -> uint8 mapping the training needs has to be derived
from the data; the percentiles computed here are that input.

Deliberately NOT done here: the uint8 conversion itself, and any resizing. Both
are modelling decisions. Storing uint16 at 512px keeps them reversible -- a
device tile is a CROP of a frame, so training must crop, and a guess baked in at
pack time could not be undone without refetching 15.5 GB.
"""
import concurrent.futures as cf
import glob, json, os, sys
import numpy as np

OUT = sys.argv[1]
JOBS = int(sys.argv[2]) if len(sys.argv) > 2 else (os.cpu_count() or 8)


def _chunk(job):
    """Fill a disjoint slice of the shards. Module level, because
    ProcessPoolExecutor pickles the callable and a closure cannot be."""
    rgb_p, lab_p, lo, paths = job
    R = np.load(rgb_p, mmap_mode="r+")      # reopened per process; a memmap
    L = np.load(lab_p, mmap_mode="r+")      # must not be shared across fork
    for k, f in enumerate(paths):
        with np.load(f) as z:
            R[lo + k] = np.transpose(z["rgb"], (1, 2, 0))   # (3,H,W)->(H,W,3)
            L[lo + k] = z["label"]
    R.flush(); L.flush()
    return len(paths)


def pack(split):
    files = sorted(glob.glob(os.path.join(OUT, split, "*.npz")))
    if not files:
        print(f"  {split}: nothing fetched, skipping"); return
    rgb_p = os.path.join(OUT, f"{split}_rgb.npy")
    lab_p = os.path.join(OUT, f"{split}_label.npy")
    if os.path.exists(rgb_p) and os.path.exists(lab_p):
        print(f"  {split}: shards exist, skipping ({len(files)} files)"); return
    n = len(files)
    # Allocate first, then let worker PROCESSES fill disjoint slices. Each
    # sample is an independent zlib stream, so this parallelises cleanly and is
    # the one part of the job that wants many cores -- 10,000 serial
    # decompressions is otherwise the longest step by far.
    np.lib.format.open_memmap(rgb_p, mode="w+", dtype=np.uint16,
                              shape=(n, 512, 512, 3)).flush()
    np.lib.format.open_memmap(lab_p, mode="w+", dtype=np.uint8,
                              shape=(n, 512, 512)).flush()
    nj = max(1, min(JOBS, n))
    edges = np.linspace(0, n, nj + 1).astype(int)
    jobs = [(rgb_p, lab_p, int(a), files[int(a):int(b)])
            for a, b in zip(edges[:-1], edges[1:]) if b > a]
    done = 0
    with cf.ProcessPoolExecutor(max_workers=nj) as ex:
        for got in ex.map(_chunk, jobs):
            done += got
            print(f"  {split}: {done}/{n}", flush=True)
    print(f"  {split}: packed {n} -> {rgb_p} ({len(jobs)} procs)", flush=True)


def measure():
    # The device delivers uint8 tiles whose measured domain is mean
    # 94.4/104.5/111.2, std 50.4/34.8/29.6 per band (pooled over 100 real
    # calibrated tiles from four scenes). Matching CloudSEN12 to THAT is what
    # makes the model transfer, and these percentiles are what the affine gets
    # fitted against.
    rgb_p = os.path.join(OUT, "train_rgb.npy")
    if os.path.exists(rgb_p):
        R = np.load(rgb_p, mmap_mode="r")
        idx = np.linspace(0, len(R) - 1, min(400, len(R))).astype(int)
        take = np.asarray(R[idx]).reshape(-1, 3).astype(np.float32)
        st = {"n_sampled": int(len(take)),
              "device_target_mean": [94.4, 104.5, 111.2],
              "device_target_std": [50.4, 34.8, 29.6],
              "mean": take.mean(0).tolist(), "std": take.std(0).tolist()}
        for q in (1, 2, 50, 98, 99):
            st[f"p{q}"] = np.percentile(take, q, axis=0).tolist()
        json.dump(st, open(os.path.join(OUT, "stats.json"), "w"), indent=1)
        print("\nreflectance stats (uint16), per band R,G,B:")
        for k in ("p1", "p2", "mean", "p50", "p98", "p99", "std"):
            print(f"  {k:6} {[round(v,1) for v in st[k]]}")
        print(f"  device target mean {st['device_target_mean']} "
              f"std {st['device_target_std']}")

    # 0 clear, 1 thick cloud, 2 thin cloud, 3 cloud shadow. Thin cloud and
    # shadow are the classes that need context rather than brightness, i.e. the
    # ones that give model capacity something to buy.
    lab_p = os.path.join(OUT, "train_label.npy")
    if os.path.exists(lab_p):
        L = np.load(lab_p, mmap_mode="r")
        idx = np.linspace(0, len(L) - 1, min(400, len(L))).astype(int)
        cnt = np.bincount(np.asarray(L[idx]).ravel(), minlength=4).astype(float)
        print("\nlabel mix (train sample):")
        for i, nm in enumerate(["clear", "thick cloud", "thin cloud",
                                "cloud shadow"]):
            print(f"  {i} {nm:13} {100*cnt[i]/cnt.sum():5.2f}%")


if __name__ == "__main__":
    for sp in ("train", "validation", "test"):
        pack(sp)
    measure()
PYEOF

if [ "$STAGE" = "all" ] || [ "$STAGE" = "pack" ]; then
  echo "=== pack + measure domain ($PACKJOBS procs) ==="
  $PY "$OUT/_pack.py" "$OUT" "$PACKJOBS" || exit 1
fi

echo "=== done ==="
du -sh "$OUT" 2>/dev/null
ls -lh "$OUT"/*.npy 2>/dev/null | awk '{print "  ", $9, $5}'
echo
echo "Next: the GPU job trains from ${OUT}/{train,validation}_{rgb,label}.npy"
echo "and stats.json gives the reflectance->device affine to fit against."
