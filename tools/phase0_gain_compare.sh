#!/bin/bash
# Phase 0: freeze the pipeline rendering by measurement, cheaply. Run ON desktop1.
#
# Gain 0.060 was chosen by a sweep in which segmentation was scored by a
# separability PROXY, because scoring it properly needs a model trained at each
# candidate. This closes that gap with real IoU -- but at smoke scale first, so
# no one spends hours on a run that might be broken.
#
# Three rungs of commitment, each gated on the previous:
#   1. SMOKE     3 epochs, 200 scenes, gain 0.060           ~1 min
#                proves the loader, the new affine and the export chain work
#   2. COMPARE   12 epochs, 1500 scenes, gain 0.050 vs 0.060  ~10 min each
#                enough training to separate the two if the effect is real
#   3. (nothing) the full rungs are Phase 2, after classification is frozen
#
# The comparison uses the plain w08d3@96 U-Net: cheapest architecture, and the
# one whose NPU behaviour is already proven (4.5 ms/tile, 97% masked on a full
# cloud frame). IoU is scored on the VALIDATION split, not test.
set -u
REMOTE=roba-desktop
REPO=/home/roba/dipp-paper/DIPP
DATA=${CLOUDSEN12_DIR:-$HOME/dipp-datasets/cloudsen12}
WORK=$HOME/cs12-train; CODE=$WORK/code; OUT=$WORK/out; LOGS=$WORK/logs
SIF=${SIF:-$HOME/sipp-cloud-training/tf215.sif}
export PYTHONNOUSERSITE=1
export APPTAINER_NO_MOUNT="${APPTAINER_NO_MOUNT:-bind-paths}"

mkdir -p "$CODE" "$OUT" "$LOGS" || exit 1
for f in train_rgb.npy train_label.npy validation_rgb.npy validation_label.npy; do
  [ -f "$DATA/$f" ] || { echo "FATAL: missing $DATA/$f"; exit 1; }
done
rsync -a "$REMOTE:$REPO/training/train_cloud.py" "$CODE/" || exit 1
grep -q 'mode.startswith("gain:")' "$CODE/train_cloud.py" \
  || { echo "FATAL: train_cloud.py lacks the gain:<G> affine"; exit 1; }

train() {  # tag gain epochs limit
  local tag=$1 gain=$2 ep=$3 lim=$4
  apptainer exec --nv --env CLOUDSEN12_DIR="$DATA" --env PYTHONNOUSERSITE=1 "$SIF" \
    python "$CODE/train_cloud.py" --dataset cloudsen12 --effort low \
      --backbone plain --res 96 --width 8 --depth 3 --batch 16 \
      --affine "gain:$gain" --loss bcedice --patience 12 \
      --epochs "$ep" --limit "$lim" --crops 3 --tag "$tag" --out "$OUT" \
      > "$LOGS/$tag.log" 2>&1
  local iou; iou=$(grep -aoE 'best val_iou=[0-9.]+' "$LOGS/$tag.log" | tail -1)
  if [ -z "$iou" ] || [ ! -f "$OUT/cloud_$tag.weights.npz" ]; then
    echo "FAILED $tag -- last lines:"; tail -n 12 "$LOGS/$tag.log" | sed 's/^/    /'; return 1
  fi
  echo "  $tag  $iou"
}

echo "=== 1/2 smoke: 3 epochs, 200 scenes, gain 0.060 (~1 min) ==="
train p0_smoke_g060 0.060 3 200 || { echo "smoke FAILED; nothing else runs"; exit 1; }
grep -q "gain=\[0.06 0.06 0.06\]" "$LOGS/p0_smoke_g060.log" \
  || { echo "FATAL: the new affine did not apply (no 'gain=[0.06 0.06 0.06]' in log)"; exit 1; }
echo "  affine confirmed in log; chain works"

echo "=== 2/2 compare: 12 epochs, 1500 scenes, both gains (~10 min each) ==="
train p0_cmp_g050 0.050 12 1500 || exit 1
train p0_cmp_g060 0.060 12 1500 || exit 1

rsync -a "$OUT"/cloud_p0_*.weights.npz "$OUT"/cloud_p0_*.json "$LOGS"/p0_*.log \
      "$REMOTE:$REPO/training/out/" || exit 1
echo "=== pushed; score both on the VALIDATION split on roba-desktop ==="
echo "  (training val_iou above is on CloudSEN12 validation already -- the"
echo "   number that decides is the one from cs12_validate.py after PTQ export)"
