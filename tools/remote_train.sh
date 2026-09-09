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
SIF=$WORK/tf215.sif

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
# Offline fallback for the QAT packages, in case pip cannot reach PyPI.
mkdir -p "$WORK/wheels"
rsync -a "$REMOTE:$REPO/tools/wheels/" "$WORK/wheels/" 2>/dev/null || true
echo "patches: $(ls "$DSET/38-Cloud_training/train_red" 2>/dev/null | wc -l) red tiles"

echo "=== 3/5 container ==="

if [ ! -f "$SIF" ]; then
  # TF 2.15, not 2.17: tfmot imports `keras` directly rather than `tf.keras`,
  # so TF_USE_LEGACY_KERAS cannot redirect it and it picks up Keras 3, which
  # has no keras.backend.unique_object_name. TF 2.15 ships Keras 2 as `keras`,
  # so tfmot works with no shim -- and its CUDA 12.2 still covers the L40S
  # (compute 8.9).
  apptainer pull "$SIF" docker://tensorflow/tensorflow:2.15.0-gpu || exit 1
fi
apptainer exec --nv "$SIF" python -c \
  "import tensorflow as tf;print('tf',tf.__version__);print(tf.config.list_physical_devices('GPU'))" \
  || { echo "GPU not visible inside container"; exit 1; }
# Apptainer bind-mounts $HOME, so --user persists outside the image.
# The container cannot resolve DNS ("Name or service not known" for every host),
# so install from the vendored wheels FIRST and only reach for PyPI if they are
# missing. Trying the network first costs a minute of retries for nothing.
#
# --no-deps either way: tf-keras declares tensorflow as a dependency, so a plain
# install pulls the whole ~600 MB TF stack into a container that already has it.
apptainer exec --nv "$SIF" pip install --user --quiet pillow 2>/dev/null || true
# Install each wheel by explicit path. A name-based install can silently
# satisfy some packages and skip others: the previous run ended with tfmot
# present but tf_keras missing, so TF_USE_LEGACY_KERAS=1 pointed at a package
# that did not exist and tfmot failed on "Keras cannot be imported".
echo "wheels available:"; ls -1 "$WORK"/wheels/*.whl 2>/dev/null || echo "  (none)"
for w in "$WORK"/wheels/*.whl; do
  [ -e "$w" ] || { echo "FATAL: no vendored wheels at $WORK/wheels"; exit 1; }
  apptainer exec --nv "$SIF" pip install --user --quiet --no-deps --no-index "$w" \
    || { echo "FATAL: could not install $(basename "$w")"; exit 1; }
done
# Verify each import separately so the failing one is named.
apptainer exec --nv "$SIF" python -c \
  "import keras; print('keras', keras.__version__)" \
  || { echo "FATAL: keras missing"; exit 1; }

# Fail here rather than after ten runs: without tfmot every --qat config dies on
# the import, and the last sweep burned all ten producing nothing.
apptainer exec --nv "$SIF" python -c \
  "import tensorflow_model_optimization as t, tensorflow as tf
m = tf.keras.Sequential([tf.keras.layers.Input((8,8,3)), tf.keras.layers.Conv2D(4,3),
                         tf.keras.layers.GlobalAveragePooling2D(), tf.keras.layers.Dense(2)])
t.quantization.keras.quantize_model(m)
print('tfmot', t.__version__, 'ok under tf', tf.__version__)" \
  || { echo "FATAL: tfmot unusable; QAT cannot run"; exit 1; }

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
# The low rung must be the small plain U-Net, not a MobileNet: the paper's low
# segmentation model is 75 KB, while the smallest MobileNet-backed candidate is
# 1360 KB and lands within 0.04 IoU of the high rung. Two alpha=0.5 configs are
# repeated here because their first run stopped at 10-12 epochs on patience 6.
#
#      backbone alpha res batch dec width depth tag
GRID="
plain 0    96  16 128 8  3 p_w08d3_r096
plain 0    128 16 128 8  3 p_w08d3_r128
plain 0    128 16 128 16 3 p_w16d3_r128
plain 0    160 16 128 16 4 p_w16d4_r160
plain 0    96  16 128 4  2 p_w04d2_r096
mobilenetv2 0.5 128 16 128 0 0 g2_a05_r128
mobilenetv2 0.5 160 16 128 0 0 g2_a05_r160
"
echo "$GRID" | while read -r spec; do
  [ -z "$spec" ] && continue
  set -- $spec
  if [ -f "$OUT/cloud_$8.weights.npz" ]; then
    echo "SKIP $8 (already done)"; continue
  fi
  echo "--- training $8 ($1 alpha=$2 res=$3 w=$6 d=$7) ---"
  apptainer exec --nv \
    --env CLOUD38_DIR="$DSET/38-Cloud_training" \
    "$SIF" python "$CODE/train_cloud.py" \
      --effort high --backbone mobilenetv2 \
      --backbone "$1" --alpha "$2" --res "$3" --batch "$4" --dec-max "$5" \
      --width "$6" --depth "$7" --loss bcedice --patience 12 \
      --qat --tag "$8" --limit 5155 --epochs 50 --out "$OUT" \
      > "$LOGS/ds_$8.log" 2>&1
  echo "DONE $8 $(grep -aoE 'best val_iou=[0-9.]+' "$LOGS/ds_$8.log" | tail -1)"
  # Push each result as it lands, so an interrupted sweep still delivers what
  # it finished rather than nothing.
  rsync -a "$OUT/cloud_$8.weights.npz" "$OUT/cloud_$8.json" "$LOGS/ds_$8.log" \
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
