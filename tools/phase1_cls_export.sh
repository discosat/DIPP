#!/bin/bash
# Phase 1, host side: export the EuroSAT-MS classifiers under TF 2.9.1 (the
# board's TFLite) and score every export on the 5,400-image holdout.
#
# Run on roba-desktop after tools/phase1_cls_train.sh has pushed weights into
# training/out/. Idempotent: an export newer than its weights is reused.
#
# Float weights are converted with PTQ (calibrated on EuroSAT-MS at the model's
# own gain, ramp-pinned to the (2/255,128) contract); *_qat weights convert
# from their learned ranges. Both land in the same table so the QAT-vs-PTQ
# decision per rung is made on the measured holdout, not assumed.
set -u
cd "$(dirname "$0")/.." || exit 1
T29=${T29:-$HOME/dipp1-experiments/tensorflow-conversion/tf_3_9_1.venv/bin/python}
PY=${PY:-$PWD/../.venv-tflite/bin/python}
RESULTS=analysis/stage4.5-models/phase1_cls_holdout.json
PAT=${1:-"training/out/landcover_ms_*.weights.npz"}

"$T29" -c "import tensorflow as tf; assert tf.__version__.startswith('2.9'), tf.__version__" 2>/dev/null \
  || { echo "FATAL: $T29 is not TF 2.9"; exit 1; }
rm -f "$RESULTS"
n=0
for w in $PAT; do
  [ -f "$w" ] || continue
  t=${w%.weights.npz}.tflite
  if [ ! -f "$t" ] || [ "$w" -nt "$t" ]; then
    echo "--- export $(basename "$w")"
    "$T29" training/convert_tf29.py "$w" -o "$t" 2>&1 | grep -aE "^(calib|rebuilt|QAT|wrote|  in |  out |.*[Ee]rror|.*mismatch)" \
      | sed 's/^/    /'
    [ -f "$t" ] || { echo "FAILED to export $w"; exit 1; }
  fi
  n=$((n+1))
done
[ "$n" -gt 0 ] || { echo "no weights match $PAT"; exit 1; }

echo "=== holdout: EuroSAT-MS testing, 5,400 images, raw uint8 at each model's gain ==="
"$PY" analysis/eurosat_ms_holdout.py ${PAT//.weights.npz/.tflite} --json "$RESULTS" 2>&1 \
  | grep -av INFO || exit 1
echo "written $RESULTS"
