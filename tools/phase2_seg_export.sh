#!/bin/bash
# Phase 2, host side: PTQ-export the gain-0.050 segmentation rungs under
# TF 2.9.1, assert they are NPU-runnable and on the passthrough contract, and
# score them against the expert masks on the VALIDATION split.
#
# Run on roba-desktop after tools/phase2_seg_train.sh has pushed weights into
# training/out/. Then tools/phase2_seg_board.sh for warm latency and energy.
#
# Calibration is CloudSEN12 rendered at the model's own gain (--calib cs12 with
# the json's affine), ramp-pinned so the input quantisation lands on
# (2/255, 128). Not --calib device: those tiles go through the OLD calibration
# and are the wrong distribution for a gain-0.050 model.
set -u
cd "$(dirname "$0")/.." || exit 1
T29=${T29:-$HOME/dipp1-experiments/tensorflow-conversion/tf_3_9_1.venv/bin/python}
PY=${PY:-$PWD/../.venv-tflite/bin/python}
OUTDIR=analysis/stage4.5-models/cs12_rungs_g050
SPLIT=${SPLIT:-validation}
PAT=${1:-"training/out/cloud_cs12g050_*.weights.npz"}

"$T29" -c "import tensorflow as tf; assert tf.__version__.startswith('2.9'), tf.__version__" 2>/dev/null \
  || { echo "FATAL: $T29 is not TF 2.9"; exit 1; }
mkdir -p "$OUTDIR"
n=0
for w in $PAT; do
  [ -f "$w" ] || continue
  tag=$(basename "$w" .weights.npz); tag=${tag#cloud_}
  t=$OUTDIR/ptq_$tag.tflite
  if [ ! -f "$t" ] || [ "$w" -nt "$t" ]; then
    echo "--- export $tag  ($(grep -o '"val_iou": [0-9.]*' "${w%.weights.npz}.json"))"
    "$T29" training/convert_tf29.py "$w" -o "$t" --calib cs12 --calib-affine gain:0.050 --samples 150 2>&1 \
      | grep -aE "^(calib|cs12 calib|wrote|  in |  out |.*[Ee]rror|.*mismatch)" | sed 's/^/    /'
    [ -f "$t" ] || { echo "FAILED to export $w"; exit 1; }
  fi
  n=$((n+1))
done
[ "$n" -gt 0 ] || { echo "no weights match $PAT"; exit 1; }

echo "=== graph checks: TRANSPOSE=0 dynamic=0 DELEGATE=1, input q=(2/255,128) ==="
"$PY" - "$OUTDIR" <<'EOF' || exit 1
import sys, os, glob, collections
from ai_edge_litert.interpreter import Interpreter
bad = 0
for p in sorted(glob.glob(os.path.join(sys.argv[1], "ptq_*.tflite"))):
    it = Interpreter(model_path=p); it.allocate_tensors()
    ops = collections.Counter(o["op_name"] for o in it._get_ops_details())
    di = it.get_input_details()[0]; s, z = di["quantization"]
    tr = ops.get("TRANSPOSE", 0); dyn = sum(ops.get(k, 0) for k in ("SHAPE", "STRIDED_SLICE", "PACK"))
    ok_npu = tr == 0 and dyn == 0
    ok_q = abs(s - 2/255) < 1e-4 and abs(z - 128) <= 2
    bad += (not ok_npu) or (not ok_q)
    print(f"  {os.path.basename(p):36} in {tuple(int(v) for v in di['shape'])} TRANSPOSE={tr} dynamic={dyn} "
          f"q=({s:.6f},{z}) {'ok' if ok_npu and ok_q else 'FAIL'}")
sys.exit(1 if bad else 0)
EOF

echo "=== expert-mask IoU on $SPLIT (select here; test is quoted once at the end) ==="
"$PY" analysis/cs12_validate.py --models "$OUTDIR" --affine gain:0.050 --split "$SPLIT" \
  -o "analysis/stage4.5-models/cs12_validation_g050_$SPLIT.json" 2>&1 | grep -av INFO
