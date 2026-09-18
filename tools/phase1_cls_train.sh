#!/bin/bash
# Phase 1: retrain the classification rungs on EuroSAT-MS at the frozen gain.
# Run ON desktop1 (2070 Ti, tf215.sif). Everything is gated: nothing long
# starts until a one-minute smoke has proven the chain inside the container.
#
#   1. SMOKE   custom_conv@64, 2000 images, 2 epochs, then a 1-epoch QAT pass
#              -- proves loader, rendering, Keras-2/tfmot in the container
#   2. FLOAT   the three deployed architectures at 224 (cost-identical rungs)
#              custom_conv ~3 min . MobileNetV2 a=0.5 ~12 min . a=1.4 ~30 min
#   3. QAT     fine-tune each from its float weights, 5 epochs, low lr
#              (PTQ capped a=1.4 at 78.4% on EuroSAT-JPEG; QAT gave 98.17%)
#   4. RES64   optional -- the same three at native 64 px (STAGES=res64)
#
# Weights + json are pushed back to roba-desktop as each lands; conversion to
# TFLite happens THERE under TF 2.9.1 (the board's version) with
# tools/phase1_cls_export.sh, never here.
#
#   STAGES="smoke float qat" ./phase1_cls_train.sh      # default
#   STAGES="res64"           ./phase1_cls_train.sh      # the resolution experiment
#   STAGES=med MED_TARGET=0.90 ./phase1_cls_train.sh   # medium rung as an operating point
#   STAGES=med MED_INIT=~/cs12-train/out/snap/ms_medop/ep05_val0.89xx.weights.npz \
#                            ./phase1_cls_train.sh      # QAT from a chosen snapshot
#
# The MEDIUM rung is an operating point, not a ceiling: alpha=0.5 trained to
# exhaustion can land within noise of alpha=1.4. Every epoch is snapshotted
# (out/snap/<tag>/epNN_val0.XXXX.weights.npz, pushed back), so the medium
# rung's epoch is chosen AFTER the float results are in, then QAT'd from it.
set -u
REMOTE=roba-desktop
REPO=/home/roba/dipp-paper/DIPP
DATA=${EUROSAT_MS:-$HOME/dipp-datasets/eurosat-ms/eurosat_ms.npz}
WORK=$HOME/cs12-train; CODE=$WORK/code; OUT=$WORK/out; LOGS=$WORK/logs
SIF=${SIF:-$HOME/sipp-cloud-training/tf215.sif}
GAIN=0.050                         # frozen in flight-config/module_1.yaml
STAGES=${STAGES:-"smoke float qat"}
export APPTAINER_NO_MOUNT="${APPTAINER_NO_MOUNT:-bind-paths}"

mkdir -p "$CODE" "$OUT" "$LOGS" "$(dirname "$DATA")" || exit 1
rsync -a "$REMOTE:$REPO/training/train_landcover_ms.py" "$CODE/" || exit 1
grep -q '"--qat"' "$CODE/train_landcover_ms.py" \
  || { echo "FATAL: train_landcover_ms.py lacks --qat (stale copy)"; exit 1; }
if [ ! -f "$DATA" ]; then
  echo "fetching EuroSAT-MS (638 MB) ..."
  rsync -a --progress "$REMOTE:$DATA" "$DATA" || exit 1
fi
[ -f "$SIF" ] || { echo "FATAL: no container at $SIF"; exit 1; }
# tfmot is not in the image; the vendored wheels (tools/wheels: tfmot 0.7.5 +
# dm_tree, no shim needed since TF 2.15 ships Keras 2) install into ~/.local,
# which is why PYTHONNOUSERSITE must NOT be set for the container -- that flag
# was for the cluster's system python and hid the wheels last time.
mkdir -p "$WORK/wheels"; rsync -a "$REMOTE:$REPO/tools/wheels/" "$WORK/wheels/" || exit 1
for w in "$WORK"/wheels/*.whl; do
  apptainer exec --nv "$SIF" pip install --user --quiet --no-deps --no-index "$w" \
    || { echo "FATAL: could not install $(basename "$w")"; exit 1; }
done
apptainer exec --nv "$SIF" python -c \
  "import tensorflow_model_optimization as t, tensorflow as tf
m = tf.keras.Sequential([tf.keras.layers.Input((8,8,3)), tf.keras.layers.Conv2D(4,3),
                         tf.keras.layers.GlobalAveragePooling2D(), tf.keras.layers.Dense(2)])
t.quantization.keras.quantize_model(m)
print('tfmot', t.__version__, 'ok under tf', tf.__version__)" 2>/dev/null \
  || { echo "FATAL: tfmot unusable in the container; QAT cannot run"; exit 1; }

run() {  # tag [args...]  -> trains, prints the holdout line, pushes the result
  local tag=$1; shift
  if [ -f "$OUT/landcover_$tag.weights.npz" ] && [ -z "${FORCE:-}" ]; then
    echo "  SKIP $tag (exists)"; return 0
  fi
  apptainer exec --nv --env EUROSAT_MS="$DATA" "$SIF" \
    python "$CODE/train_landcover_ms.py" --gain "$GAIN" --tag "$tag" --out "$OUT" "$@" \
    > "$LOGS/$tag.log" 2>&1
  local line; line=$(grep -a 'TEST top-1=' "$LOGS/$tag.log" | tail -1)
  if [ -z "$line" ] || [ ! -f "$OUT/landcover_$tag.weights.npz" ]; then
    echo "FAILED $tag -- last lines:"; tail -n 15 "$LOGS/$tag.log" | sed 's/^/    /'
    return 1
  fi
  echo "  $tag  $line"
  rsync -a "$OUT/landcover_$tag.weights.npz" "$OUT/landcover_$tag.json" \
        "$LOGS/$tag.log" "$REMOTE:$REPO/training/out/" 2>/dev/null
  # Every epoch's weights go back too (named by val accuracy), so the medium
  # operating point can be picked after the fact and fed to QAT as --init.
  [ -d "$OUT/snap/$tag" ] && rsync -a "$OUT/snap/$tag" "$REMOTE:$REPO/training/out/snap/" 2>/dev/null
}

for stage in $STAGES; do case $stage in
smoke)
  echo "=== smoke: custom_conv@64, 2000 images, 2 epochs (+1 epoch QAT) ==="
  FORCE=1 run ms_smoke --arch custom_conv --res 64 --limit 2000 --epochs 2 \
    || { echo "smoke FAILED; nothing else runs"; exit 1; }
  FORCE=1 run ms_smoke_qat --arch custom_conv --res 64 --limit 2000 --epochs 1 \
    --qat --init "$OUT/landcover_ms_smoke.weights.npz" --lr 1e-4 \
    || { echo "QAT smoke FAILED (tfmot in the container?); nothing else runs"; exit 1; }
  grep -q "qat=True" "$LOGS/ms_smoke_qat.log" \
    || { echo "FATAL: QAT wrapper did not apply"; exit 1; }
  echo "  chain works in the container" ;;
float)
  echo "=== float rungs at 224, gain $GAIN ==="
  run ms_low  --arch custom_conv --res 224 --epochs 30 --patience 8 || exit 1
  run ms_med  --arch mobilenetv2 --alpha 0.5 --res 224 --epochs 30 --patience 6 || exit 1
  run ms_high --arch mobilenetv2 --alpha 1.4 --res 224 --epochs 30 --patience 6 \
    --batch 32 || exit 1 ;;
qat)
  # MED_INIT picks the medium rung's operating point: a snapshot from
  # out/snap/ms_med/ (e.g. ep06_val0.9012.weights.npz) instead of the
  # trained-to-exhaustion weights, when the latter sit too close to ms_high.
  # MED_TARGET stops the QAT pass itself at a val accuracy.
  echo "=== QAT fine-tune from float, 5 epochs, lr 1e-5 ==="
  for r in low med high; do
    [ -f "$OUT/landcover_ms_$r.weights.npz" ] || { echo "FATAL: no float ms_$r"; exit 1; }
  done
  MED_INIT=${MED_INIT:-$OUT/landcover_ms_med.weights.npz}
  [ -f "$MED_INIT" ] || { echo "FATAL: MED_INIT $MED_INIT missing"; exit 1; }
  echo "  medium rung init: $MED_INIT  target: ${MED_TARGET:-none}"
  run ms_low_qat  --arch custom_conv --res 224 --epochs 5 --patience 5 --lr 1e-4 \
    --qat --init "$OUT/landcover_ms_low.weights.npz" || exit 1
  run ms_med_qat  --arch mobilenetv2 --alpha 0.5 --res 224 --epochs 5 --patience 5 \
    --lr 1e-5 --batch 16 --qat --init "$MED_INIT" --target "${MED_TARGET:-0}" || exit 1
  run ms_high_qat --arch mobilenetv2 --alpha 1.4 --res 224 --epochs 5 --patience 5 \
    --lr 1e-5 --batch 16 --qat --init "$OUT/landcover_ms_high.weights.npz" || exit 1 ;;
med)
  # The medium rung as an OPERATING POINT. alpha=0.5 trained to exhaustion
  # scored 98.59% against alpha=1.4's 98.57% -- one rung, not two. The paper's
  # own medium figure (89.98%) was a target stop, so: float to MED_TARGET on
  # val with quarter-length epochs (a snapshot + target check every 72 steps),
  # then QAT from the result, again to the target, with short epochs so the
  # fine-tune cannot overshoot it. Snapshots of both passes are pushed back.
  MED_TARGET=${MED_TARGET:-0.90}
  echo "=== medium rung to val_accuracy $MED_TARGET (float, then QAT) ==="
  FORCE=1 run ms_medop --arch mobilenetv2 --alpha 0.5 --res 224 --epochs 60 \
    --steps-per-epoch 72 --patience 12 --target "$MED_TARGET" || exit 1
  FORCE=1 run ms_medop_qat --arch mobilenetv2 --alpha 0.5 --res 224 --epochs 20 \
    --steps-per-epoch 150 --patience 20 --lr 1e-5 --batch 16 --qat \
    --init "${MED_INIT:-$OUT/landcover_ms_medop.weights.npz}" --target "$MED_TARGET" || exit 1 ;;
res64)
  echo "=== resolution experiment: the same three at native 64 px ==="
  run ms_low64  --arch custom_conv --res 64 --epochs 30 --patience 8 || exit 1
  run ms_med64  --arch mobilenetv2 --alpha 0.5 --res 64 --epochs 30 --patience 6 || exit 1
  run ms_high64 --arch mobilenetv2 --alpha 1.4 --res 64 --epochs 30 --patience 6 || exit 1 ;;
*) echo "unknown stage $stage"; exit 1 ;;
esac; done

echo "=== summary (val / TEST top-1 per run) ==="
for f in "$LOGS"/ms_*.log; do
  printf "  %-14s %s\n" "$(basename "$f" .log)" "$(grep -a 'TEST top-1=' "$f" | tail -1)"
done
echo "next, on roba-desktop:  tools/phase1_cls_export.sh"
