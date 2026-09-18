#!/bin/bash
# Float retrain + PTQ export of the cloud segmentation rungs. Run ON desktop7.
#
# WHY FLOAT, NOT QAT. QAT is what broke NPU acceleration: tfmot wraps
# Conv2DTranspose in a form the converter cannot fold, leaving one runtime
# TRANSPOSE per decoder stage that splits the delegate partition four ways.
# Measured on the board:
#
#   our QAT seg_low          48.77 ms/tile NPU   48.52 CPU   =  1.0x  (no NPU)
#   PTQ + batch_size=1       22.30 ms/tile NPU  1245.7 CPU   = 55.7x
#
# and QAT bought nothing in quality here -- PTQ measured -0.021 IoU on the big
# model and +0.217 on the small one, while mis-calibrated. (Classification is
# the opposite case: there QAT is essential, 78.4% -> 98.17%, and stays.)
#
# WHY A RETRAIN AND NOT A RE-EXPORT. Every cloud_*.json says qat: true -- the
# float weights were overwritten by the QAT runs. There is nothing left to
# PTQ-convert from, so float has to be trained again.
#
# Two fixes have to be in the pulled code, and this script refuses to run
# without them:
#   train_cloud.py    batch_size=1 on the MobileNetV2 builder, else the export
#                     carries SHAPE/STRIDED_SLICE/PACK and the delegate refuses
#                     the graph outright ("Failed to verify graph", node 106)
#   convert_tf29.py   representative_cloud(), so PTQ calibrates on 38-Cloud in
#                     the device radiometric domain instead of EuroSAT JPEGs
#
# Safe to re-run: a rung whose weights already exist is skipped.
set -u

REMOTE=roba-desktop            # this machine, via ProxyJump planb
REPO=/home/roba/dipp-paper/DIPP
DATA=/home/roba/dipp1-experiments/tensorflow-conversion/38cloud
WORK=$HOME/sipp-cloud-training # same workspace remote_train.sh uses
CODE=$WORK/code
DSET=$WORK/data
OUT=$WORK/out
LOGS=$WORK/logs
SIF=$WORK/tf215.sif

echo "=== 1/6 code ==="
mkdir -p "$CODE" "$DSET/38-Cloud_training" "$OUT" "$LOGS" || exit 1
rsync -a "$REMOTE:$REPO/training/train_cloud.py" \
         "$REMOTE:$REPO/training/train_landcover.py" \
         "$REMOTE:$REPO/training/convert_tf29.py" "$CODE/" || exit 1
grep -q "batch_size=1" "$CODE/train_cloud.py" \
  || { echo "FATAL: train_cloud.py lacks batch_size=1 (unfixed copy)"; exit 1; }
grep -q "def representative_cloud" "$CODE/convert_tf29.py" \
  || { echo "FATAL: convert_tf29.py lacks representative_cloud (unfixed copy)"; exit 1; }
echo "  both fixes present"

echo "=== 2/6 data (incremental; already present from remote_train.sh) ==="
rsync -a --partial --info=progress2 \
  "$REMOTE:$DATA/38-Cloud_training/train_red" \
  "$REMOTE:$DATA/38-Cloud_training/train_green" \
  "$REMOTE:$DATA/38-Cloud_training/train_blue" \
  "$REMOTE:$DATA/38-Cloud_training/train_gt" \
  "$DSET/38-Cloud_training/" || exit 1
rsync -a "$REMOTE:$DATA/training_patches_38-cloud_nonempty.csv" "$DSET/" || exit 1
echo "  patches: $(ls "$DSET/38-Cloud_training/train_red" | wc -l) red tiles"

echo "=== 3/6 container ==="
[ -f "$SIF" ] || apptainer pull "$SIF" docker://tensorflow/tensorflow:2.15.0-gpu || exit 1
apptainer exec --nv "$SIF" python -c \
  "import tensorflow as tf;print('tf',tf.__version__);print(tf.config.list_physical_devices('GPU'))" \
  || { echo "GPU not visible inside container"; exit 1; }
apptainer exec --nv "$SIF" pip install --user --quiet pillow 2>/dev/null || true
# No tfmot needed: this trains float and quantises after the fact.

echo "=== 4/6 train (FLOAT) ==="
# Resolution is the rung knob, not width: on 38-Cloud, 1.4 vs 0.35 at 224 gave
# 0.027 of IoU separation while dropping resolution to 96 gave 0.193. Cost also
# scales with res^2 (96/160/224 -> 1 : 2.8 : 5.4), so resolution moves quality
# and cost together, which is what a scheduler needs from an effort level.
# Two widths at 224 so the top rung can be chosen on measurement rather than
# assumption, and one plain U-Net as a cheap reference point.
#      backbone alpha res batch dec width depth tag
GRID="
mobilenetv2 0.35 96  16 128 0  0 f_a035_r096
mobilenetv2 0.35 160 16 128 0  0 f_a035_r160
mobilenetv2 1.0  224 16 128 0  0 f_a10_r224
mobilenetv2 1.4  224 16 128 0  0 f_a14_r224
plain       0    128 16 128 16 3 f_p_w16d3_r128
"
echo "$GRID" | while read -r spec; do
  [ -z "$spec" ] && continue
  set -- $spec
  if [ -f "$OUT/cloud_$8.weights.npz" ]; then echo "SKIP $8 (done)"; continue; fi
  echo "--- $8 ($1 alpha=$2 res=$3) ---"
  apptainer exec --nv --env CLOUD38_DIR="$DSET/38-Cloud_training" "$SIF" \
    python "$CODE/train_cloud.py" --effort high \
      --backbone "$1" --alpha "$2" --res "$3" --batch "$4" --dec-max "$5" \
      --width "$6" --depth "$7" --loss bcedice --patience 12 \
      --tag "$8" --limit 5155 --epochs 40 --out "$OUT" \
      > "$LOGS/f_$8.log" 2>&1
  echo "DONE $8 $(grep -aoE 'best val_iou=[0-9.]+' "$LOGS/f_$8.log" | tail -1)"
done

echo "=== 5/6 PTQ convert + verify ==="
FAILED=""
for w in "$OUT"/cloud_f_*.weights.npz; do
  [ -e "$w" ] || { echo "FATAL: nothing trained"; exit 1; }
  stem=$(basename "$w" .weights.npz); tf="$OUT/ptq_${stem#cloud_}.tflite"
  echo "--- $stem ---"
  apptainer exec --nv --env CLOUD38_DIR="$DSET/38-Cloud_training" "$SIF" \
    python "$CODE/convert_tf29.py" "$w" -o "$tf" 2>&1 | tail -3
  [ -f "$tf" ] || { echo "  FAIL: no output"; FAILED="$FAILED $stem"; continue; }
  # The point of the exercise: one delegate partition, no TRANSPOSE, no
  # dynamic shapes. Anything else will not be accelerated on the board.
  r=$(apptainer exec "$SIF" python -c "
import collections
from tensorflow.lite.python.interpreter import Interpreter
it=Interpreter(model_path='$tf'); it.allocate_tensors()
o=collections.Counter(x['op_name'] for x in it._get_ops_details())
print(o.get('TRANSPOSE',0), sum(o.get(k,0) for k in ('SHAPE','STRIDED_SLICE','PACK')))
" 2>/dev/null | tail -1)
  set -- $r
  if [ "${1:-x}" = "0" ] && [ "${2:-x}" = "0" ]; then
    echo "  PASS: TRANSPOSE=0 dynamic=0 ($(stat -c%s "$tf") bytes)"
  else
    echo "  FAIL: TRANSPOSE=${1:-?} dynamic=${2:-?} -- would not be accelerated"
    FAILED="$FAILED $stem"
  fi
done

echo "=== 6/6 push back ==="
if [ -n "$FAILED" ]; then
  echo "NOT pushing, these would not be accelerated:$FAILED"
  exit 1
fi
rsync -a "$OUT"/ptq_f_*.tflite "$OUT"/cloud_f_*.weights.npz "$OUT"/cloud_f_*.json \
      "$LOGS"/f_*.log "$REMOTE:$REPO/training/out/" || exit 1
echo "pushed. val IoU per rung:"
for f in "$LOGS"/f_*.log; do
  printf "  %-18s %s\n" "$(basename "$f" .log)" \
    "$(grep -aoE 'best val_iou=[0-9.]+' "$f" | tail -1)"
done
