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
if [ ! -f "$SIF" ]; then
  apptainer pull "$SIF" docker://tensorflow/tensorflow:2.17.0-gpu || exit 1
fi
apptainer exec --nv "$SIF" python -c \
  "import tensorflow as tf;print('tf',tf.__version__);print(tf.config.list_physical_devices('GPU'))" \
  || { echo "GPU not visible inside container"; exit 1; }
# Apptainer bind-mounts $HOME, so --user persists outside the image.
apptainer exec --nv "$SIF" pip install --user --quiet pillow

echo "=== 4/5 train ==="
# alpha res batch tag  -- batch sized for the 2070's 8 GB; drop to 4 if it OOMs
for spec in "1.4 224 8 ds_hi" "0.35 96 16 ds_lo"; do
  set -- $spec
  if [ -f "$OUT/cloud_$4.weights.npz" ]; then
    echo "SKIP $4 (already done)"; continue
  fi
  apptainer exec --nv \
    --env TF_USE_LEGACY_KERAS=0 \
    --env CLOUD38_DIR="$DSET/38-Cloud_training" \
    "$SIF" python "$CODE/train_cloud.py" \
      --effort high --backbone mobilenetv2 \
      --alpha "$1" --res "$2" --batch "$3" --dec-max 128 --loss bcedice \
      --tag "$4" --limit 5155 --epochs 40 --out "$OUT" \
      > "$LOGS/ds_$4.log" 2>&1
  apptainer exec --nv "$SIF" python "$CODE/dump_weights.py" \
      "$OUT/cloud_$4.keras" >> "$LOGS/ds_$4.log" 2>&1
  echo "DONE $4 $(grep -aoE 'best val_iou=[0-9.]+' "$LOGS/ds_$4.log" | tail -1)"
done

echo "=== 5/5 push results back to roba-desktop ==="
# Weights and metadata only; conversion to TFLite happens on roba-desktop, as the
# board runs TFLite 2.9.1 and the converter must match that version.
rsync -a "$OUT"/cloud_ds_*.weights.npz "$OUT"/cloud_ds_*.json \
         "$LOGS"/ds_*.log "$REMOTE:$REPO/training/out/" || exit 1
echo "pushed. summary:"
grep -ahoE 'best val_iou=[0-9.]+' "$LOGS"/ds_*.log | tail -5
