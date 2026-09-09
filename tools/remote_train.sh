#!/bin/bash
# Run ON the GPU host (desktop7). Pulls code+data from this machine, trains,
# pushes results back. The GPU host reaches here as "roba-desktop", which is a
# ProxyJump entry through "planb" -- the connection is one-way, this machine
# cannot initiate to the GPU host, so every transfer starts from there.
#
# Required in the GPU host's ~/.ssh/config:
#   Host roba-desktop
#       HostName <this machine>
#       User     roba
#       ProxyJump planb
#
# Everything lives under a single directory ($WORK) so the host's home stays
# clean: code, data, container image, checkpoints and logs are all inside it,
# and removing that one directory removes every trace of this job.
#
# Safe to re-run: transfers are incremental, the container is pulled once, and a
# rung whose weights already exist is skipped rather than retrained.
set -u

REMOTE=roba-desktop      # this machine, via ProxyJump planb
REPO=/home/roba/dipp-paper/DIPP
DATA=/home/roba/dipp1-experiments/tensorflow-conversion/38cloud

WORK=$HOME/sipp-cloud-training      # override: WORK=/somewhere ./remote_train.sh
CODE=$WORK/code
DSET=$WORK/data
OUT=$WORK/out
LOGS=$WORK/logs
SIF=$WORK/tf217.sif

echo "=== 0/5 workspace: $WORK ==="
mkdir -p "$CODE" "$DSET/38-Cloud_training" "$OUT" "$LOGS" || exit 1

echo "=== 1/5 code ==="
rsync -a "$REMOTE:$REPO/training/train_cloud.py" \
         "$REMOTE:$REPO/training/train_landcover.py" \
         "$REMOTE:$REPO/training/dump_weights.py" "$CODE/" || exit 1

echo "=== 2/5 data (4.2 GB, incremental) ==="
rsync -a --partial --info=progress2 \
  "$REMOTE:$DATA/38-Cloud_training/train_red" \
  "$REMOTE:$DATA/38-Cloud_training/train_green" \
  "$REMOTE:$DATA/38-Cloud_training/train_blue" \
  "$REMOTE:$DATA/38-Cloud_training/train_gt" \
  "$DSET/38-Cloud_training/" || exit 1
# The script looks for the CSV one level ABOVE the band directories.
rsync -a "$REMOTE:$DATA/training_patches_38-cloud_nonempty.csv" "$DSET/" || exit 1
echo "patches: $(ls "$DSET/38-Cloud_training/train_red" 2>/dev/null | wc -l) red tiles"

echo "=== 3/5 container ==="
# tfmot needs Keras 2; TF 2.17 defaults to Keras 3, so pin the legacy path.

if [ ! -f "$SIF" ]; then
  apptainer pull "$SIF" docker://tensorflow/tensorflow:2.17.0-gpu || exit 1
fi
apptainer exec --nv "$SIF" python -c \
  "import tensorflow as tf;print('tf',tf.__version__);print(tf.config.list_physical_devices('GPU'))" \
  || { echo "GPU not visible inside container"; exit 1; }
# Apptainer bind-mounts $HOME, so --user persists outside the image.
apptainer exec --nv "$SIF" pip install --user --quiet \
    pillow tf-keras "tensorflow-model-optimization==0.7.5"

echo "=== 4/5 train ==="
# The rung grid. Resolution is swept more densely than width because resolution
# is what actually separates the rungs: on 38-Cloud, width alone (1.4 vs 0.35 at
# 224) gave 0.027 of IoU separation while dropping resolution to 96 gave 0.193.
# It also cuts cost and quality together, which is the property a scheduler
# needs from an effort level.
#
# Batch stays at 16 to match the runs already measured elsewhere; the L40S has
# the memory for far more, but changing the accelerator and the batch size at
# once would make the numbers incomparable.
#
#          alpha res batch dec tag
GRID="
1.4  224 16 128 g_a14_r224
1.4  192 16 128 g_a14_r192
1.0  224 16 128 g_a10_r224
1.4  224 16  64 g_a14_r224_d64
0.5  160 16 128 g_a05_r160
0.5  128 16 128 g_a05_r128
0.35 160 16 128 g_a035_r160
0.35 128 16 128 g_a035_r128
0.35 96  16 128 g_a035_r096
0.35 96  16  64 g_a035_r096_d64
"
echo "$GRID" | while read -r spec; do
  [ -z "$spec" ] && continue
  set -- $spec
  if [ -f "$OUT/cloud_$5.weights.npz" ]; then
    echo "SKIP $5 (already done)"; continue
  fi
  echo "--- training $5 (alpha=$1 res=$2 batch=$3 dec_max=$4) ---"
  apptainer exec --nv \
    --env TF_USE_LEGACY_KERAS=1 \
    --env CLOUD38_DIR="$DSET/38-Cloud_training" \
    "$SIF" python "$CODE/train_cloud.py" \
      --effort high --backbone mobilenetv2 \
      --alpha "$1" --res "$2" --batch "$3" --dec-max "$4" --loss bcedice \
      --qat --tag "$5" --limit 5155 --epochs 50 --out "$OUT" \
      > "$LOGS/ds_$5.log" 2>&1
  echo "DONE $5 $(grep -aoE 'best val_iou=[0-9.]+' "$LOGS/ds_$5.log" | tail -1)"
  # Push each result as it lands, so an interrupted sweep still delivers what
  # it finished rather than nothing.
  rsync -a "$OUT/cloud_$5.weights.npz" "$OUT/cloud_$5.json" "$LOGS/ds_$5.log" \
        "$REMOTE:$REPO/training/out/" 2>/dev/null
done

echo "=== 5/5 push results back to roba-desktop ==="
# Weights and metadata only; conversion to TFLite happens on roba-desktop, as the
# board runs TFLite 2.9.1 and the converter must match that version.
rsync -a "$OUT"/cloud_g_*.weights.npz "$OUT"/cloud_g_*.json \
         "$LOGS"/ds_*.log "$REMOTE:$REPO/training/out/" || exit 1
echo "pushed. summary (val IoU per config):"
for f in "$LOGS"/ds_g_*.log; do
  printf "  %-22s %s\n" "$(basename "$f" .log | sed s/^ds_//)" \
    "$(grep -aoE 'best val_iou=[0-9.]+' "$f" | tail -1)"
done
