#!/bin/bash
# Train the CloudSEN12 segmentation rungs on the GPU. FLOAT, not QAT.
#
# WHY FLOAT. QAT is what cost segmentation the accelerator: tfmot wraps
# Conv2DTranspose in a form the converter cannot fold, leaving one runtime
# TRANSPOSE per decoder stage that splits the delegate partition. Measured on
# the board: our QAT model 48.77 ms/tile NPU against 48.52 CPU (1.0x, i.e. the
# NPU was never used), while a float+PTQ export of the same shape runs
# 4.5-5.2 ms/tile with a 55.7x NPU speedup. QAT also bought nothing in quality
# here (-0.021 IoU on the big model, +0.217 on the small one).
# Classification is the opposite case and keeps QAT; do not generalise.
#
# WHY THE CHAIN IS ALREADY PROVEN. A 3-epoch smoke model trained this way took
# a fully-clouded frame from 0.54% masked to 97.31% on the board, against 96.58%
# on the host -- agreement to 0.73pp -- and 0.03% on a clear frame. So the only
# open question is how good the rungs get, not whether the path works.
#
# CONVERSION IS NOT DONE HERE, deliberately. PTQ calibrates on real device
# tiles, which exist only on roba-desktop, and the input range must be pinned to
# scale=2/255 / zp=128 or the module's raw-uint8 passthrough silently feeds the
# model wrong values (that cost one full board round-trip). This script pushes
# float weights back and conversion happens there.
#
# Resumable: a rung whose weights exist is skipped.
set -u

REMOTE=roba-desktop
REPO=/home/roba/dipp-paper/DIPP
DATA=${CLOUDSEN12_DIR:-$HOME/dipp-datasets/cloudsen12}
WORK=$HOME/cs12-train
CODE=$WORK/code
OUT=$WORK/out
LOGS=$WORK/logs
SIF=${SIF:-$HOME/sipp-cloud-training/tf215.sif}

export PYTHONNOUSERSITE=1
# Apptainer on desktop1 binds /run/systemd/resolve from apptainer.conf and dies
# if that path is absent ("mount source /run/systemd/resolve doesn't exist"),
# which happens whenever systemd-resolved is not running. Disabling the
# config-file bind paths avoids it; $HOME and the GPU come through separate
# mechanisms, and this job needs no DNS -- everything it reads is a local file.
export APPTAINER_NO_MOUNT="${APPTAINER_NO_MOUNT:-bind-paths}"

echo "=== 1/4 checks ==="
mkdir -p "$CODE" "$OUT" "$LOGS" || exit 1
for f in train_rgb.npy train_label.npy validation_rgb.npy validation_label.npy; do
  [ -f "$DATA/$f" ] || { echo "FATAL: missing $DATA/$f -- run pack_cloudsen12.py"; exit 1; }
done
echo "  data: $(du -sh "$DATA" | cut -f1) at $DATA"
[ -f "$SIF" ] || { echo "FATAL: no container at $SIF (override with SIF=...)"; exit 1; }

rsync -a "$REMOTE:$REPO/training/train_cloud.py" "$CODE/" || exit 1
grep -q "batch_size=1" "$CODE/train_cloud.py" \
  || { echo "FATAL: train_cloud.py lacks batch_size=1; that omission is what made"
       echo "       every g-family model fail VX graph verification"; exit 1; }
grep -q "def load_cloudsen12" "$CODE/train_cloud.py" \
  || { echo "FATAL: train_cloud.py has no CloudSEN12 loader"; exit 1; }
echo "  code ok"

apptainer exec --nv "$SIF" python -c \
  "import tensorflow as tf; print('  tf', tf.__version__, tf.config.list_physical_devices('GPU'))" \
  || { echo "FATAL: GPU not visible in container"; exit 1; }

echo "=== 2/4 train ==="
# Resolution is the rung knob. On 38-Cloud, width 1.4-vs-0.35 at 224 gave 0.027
# of IoU separation while dropping resolution to 96 gave 0.193, and cost scales
# as res^2 -- so resolution moves quality and cost together, which is what an
# effort level needs. Both families are trained so the ladder can be picked from
# a measured Pareto front rather than an assumption: plain U-Nets are cheap
# (~4.6 ms/tile at 96) and MobileNetV2 reaches 22.3 ms/tile at 224, which is a
# ~5x span to choose three rungs from.
#
# limit/crops are per-rung because X is materialised in host RAM as float32:
# 12,000 samples at 224 is 7.2 GB for X alone, so the big rungs take fewer
# crops. batch 8 at 224 for the 2070's 8 GB (drop to 4 if it OOMs).
#
#      backbone alpha res batch width depth limit crops tag
GRID="
plain        0    96  16 8  3 4000 4 cs12_p_w08d3_r096
plain        0    128 16 16 3 4000 3 cs12_p_w16d3_r128
plain        0    160 16 16 4 3000 3 cs12_p_w16d4_r160
mobilenetv2  0.35 160 16 0  0 3000 3 cs12_g_a035_r160
mobilenetv2  1.0  224 8  0  0 3000 2 cs12_g_a10_r224
mobilenetv2  1.4  224 8  0  0 3000 2 cs12_g_a14_r224
"
echo "$GRID" | while read -r spec; do
  [ -z "$spec" ] && continue
  set -- $spec
  tag=$9
  if [ -f "$OUT/cloud_$tag.weights.npz" ]; then echo "SKIP $tag"; continue; fi
  echo "--- $tag ($1 alpha=$2 res=$3 batch=$4 limit=$7 crops=$8) ---"
  apptainer exec --nv --env CLOUDSEN12_DIR="$DATA" --env PYTHONNOUSERSITE=1 \
    "$SIF" python "$CODE/train_cloud.py" \
      --dataset cloudsen12 --effort high \
      --backbone "$1" --alpha "$2" --res "$3" --batch "$4" \
      --width "$5" --depth "$6" --limit "$7" --crops "$8" \
      --loss bcedice --patience 12 --epochs 40 --dec-max 128 \
      --tag "$tag" --out "$OUT" > "$LOGS/$tag.log" 2>&1
  echo "DONE $tag $(grep -aoE 'best val_iou=[0-9.]+' "$LOGS/$tag.log" | tail -1)"
  # Push as each lands, so an interrupted sweep still delivers what finished.
  rsync -a "$OUT/cloud_$tag.weights.npz" "$OUT/cloud_$tag.json" \
        "$LOGS/$tag.log" "$REMOTE:$REPO/training/out/" 2>/dev/null
done

echo "=== 3/4 push ==="
rsync -a "$OUT"/cloud_cs12_*.weights.npz "$OUT"/cloud_cs12_*.json \
      "$LOGS"/cs12_*.log "$REMOTE:$REPO/training/out/" || exit 1

echo "=== 4/4 summary ==="
for f in "$LOGS"/cs12_*.log; do
  printf "  %-22s %s\n" "$(basename "$f" .log)" \
    "$(grep -aoE 'best val_iou=[0-9.]+' "$f" | tail -1)"
done
echo "=== ALL DONE ==="
