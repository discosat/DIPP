#!/bin/bash
# Phase 2: retrain the cloud-segmentation rungs on CloudSEN12 at the frozen
# rendering (gain 0.050, zero offset -- flight-config/module_1.yaml). Run ON
# desktop7 (2070, tf215.sif). Smoke-gated, like phase1_cls_train.sh.
#
# Why retrain: the six candidates measured at IoU 0.73-0.82 were trained
# through the OLD affine; a segmenter is only valid in the rendering it was
# trained in, and Phase 0 froze a different one.
#
# What is retrained -- a ladder, not the whole grid. Previous val IoU (old
# rendering) and known NPU cost:
#     plain  w08d3 @96     0.731    ~4.5 ms/tile      <- low
#     plain  w16d3 @128    0.749    (unmeasured)      <- backup mid
#     mnv2   a0.35 @160    0.792    (unmeasured)      <- mid
#     mnv2   a1.4  @224    0.817    ~30 ms/tile       <- high
# a1.0@224 (0.811) is 0.006 below a1.4: not a rung. Float only, never QAT:
# tfmot's Conv2DTranspose wrapping leaves runtime TRANSPOSE ops that split the
# NPU partition and cost a 55x speedup. PTQ export happens on roba-desktop
# under TF 2.9.1 with the ramp-pinned (2/255,128) contract.
#
#   STAGES="smoke ladder" ./phase2_seg_train.sh     # default, ~1.5 h
#   STAGES="smoke qat"    ./phase2_seg_train.sh     # selective QAT of the two MobileNet rungs
#   STAGES=ladder ONLY=cs12g050_g_a14_r224 ./phase2_seg_train.sh
set -u
REMOTE=roba-desktop
REPO=/home/roba/dipp-paper/DIPP
DATA=${CLOUDSEN12_DIR:-$HOME/dipp-datasets/cloudsen12}
WORK=$HOME/cs12-train; CODE=$WORK/code; OUT=$WORK/out; LOGS=$WORK/logs
SIF=${SIF:-$HOME/sipp-cloud-training/tf215.sif}
GAIN=0.050
STAGES=${STAGES:-"smoke ladder"}
ONLY=${ONLY:-}
export APPTAINER_NO_MOUNT="${APPTAINER_NO_MOUNT:-bind-paths}"

mkdir -p "$CODE" "$OUT" "$LOGS" || exit 1
for f in train_rgb.npy train_label.npy validation_rgb.npy validation_label.npy; do
  [ -f "$DATA/$f" ] || { echo "FATAL: missing $DATA/$f"; exit 1; }
done
rsync -a "$REMOTE:$REPO/training/train_cloud.py" "$CODE/" || exit 1
grep -q 'mode.startswith("gain:")' "$CODE/train_cloud.py" \
  || { echo "FATAL: train_cloud.py lacks the gain:<G> affine"; exit 1; }
grep -q 'def qat_wrap' "$CODE/train_cloud.py" \
  || { echo "FATAL: train_cloud.py lacks qat_wrap (stale copy)"; exit 1; }
[ -f "$SIF" ] || { echo "FATAL: no container at $SIF"; exit 1; }

train() {  # tag backbone alpha res batch width depth limit crops epochs [extra args...]
  local tag=$1 bb=$2 alpha=$3 res=$4 batch=$5 w=$6 d=$7 lim=$8 crops=$9 ep=${10}
  shift 10
  [ -n "$ONLY" ] && [ "$ONLY" != "$tag" ] && return 0
  if [ -f "$OUT/cloud_$tag.weights.npz" ] && [ -z "${FORCE:-}" ]; then
    echo "  SKIP $tag (exists)"; return 0
  fi
  apptainer exec --nv --env CLOUDSEN12_DIR="$DATA" "$SIF" \
    python "$CODE/train_cloud.py" --dataset cloudsen12 --effort high \
      --backbone "$bb" --alpha "$alpha" --res "$res" --batch "$batch" \
      --width "$w" --depth "$d" --limit "$lim" --crops "$crops" \
      --affine "gain:$GAIN" --loss bcedice --patience 12 --epochs "$ep" \
      --dec-max 128 --tag "$tag" --out "$OUT" "$@" > "$LOGS/$tag.log" 2>&1
  local iou; iou=$(grep -aoE 'best val_iou=[0-9.]+' "$LOGS/$tag.log" | tail -1)
  if [ -z "$iou" ] || [ ! -f "$OUT/cloud_$tag.weights.npz" ]; then
    echo "FAILED $tag -- last lines:"; tail -n 12 "$LOGS/$tag.log" | sed 's/^/    /'; return 1
  fi
  echo "  $tag  $iou"
  rsync -a "$OUT/cloud_$tag.weights.npz" "$OUT/cloud_$tag.json" "$LOGS/$tag.log" \
        "$REMOTE:$REPO/training/out/" 2>/dev/null
}

for stage in $STAGES; do case $stage in
smoke)
  echo "=== smoke: w08d3@96, 3 epochs, 200 scenes, gain $GAIN (~1 min) ==="
  FORCE=1 train p2_smoke plain 0 96 16 8 3 200 3 3 \
    || { echo "smoke FAILED; nothing else runs"; exit 1; }
  grep -q "gain=\[0.05 0.05 0.05\]" "$LOGS/p2_smoke.log" \
    || { echo "FATAL: the affine did not apply (no 'gain=[0.05 0.05 0.05]' in log)"; exit 1; }
  echo "  affine confirmed; chain works"
  echo "=== smoke: selective QAT, a0.35@160, 1 epoch, 200 scenes (~1 min) ==="
  FORCE=1 train p2_smoke_qat mobilenetv2 0.35 160 16 0 0 200 2 1 \
    --qat selective --lr 1e-4 \
    || { echo "QAT smoke FAILED (tfmot / clone_model in the container?); nothing else runs"; exit 1; }
  grep -q "QAT wrapped (selective)" "$LOGS/p2_smoke_qat.log" \
    || { echo "FATAL: selective wrap did not apply"; exit 1; }
  echo "  selective QAT wraps in the container" ;;
ladder)
  echo "=== ladder at gain $GAIN (cheapest first, so a failure costs least) ==="
  #     tag                   backbone    alpha res batch w  d  limit crops epochs
  train cs12g050_p_w08d3_r096 plain       0    96  16  8  3  4000  4  40 || exit 1
  train cs12g050_p_w16d3_r128 plain       0    128 16  16 3  4000  3  40 || exit 1
  train cs12g050_g_a035_r160  mobilenetv2 0.35 160 16  0  0  3000  3  40 || exit 1
  train cs12g050_g_a14_r224   mobilenetv2 1.4  224 8   0  0  3000  2  40 || exit 1 ;;
qat)
  # PTQ costs the MobileNet-encoder rungs 0.15 (a0.35) and 0.07 (a1.4) IoU on
  # validation, against <=0.012 for the plain U-Nets. Selective QAT: every
  # layer but Conv2DTranspose learns its ranges (the export then carries
  # TRANSPOSE_CONV, TRANSPOSE=0 -- verified on the host before this ran).
  # Fine-tune from the float weights, short and at a low lr.
  echo "=== selective QAT from float, 8 epochs, lr 1e-4 ==="
  for r in g_a035_r160 g_a14_r224; do
    [ -f "$OUT/cloud_cs12g050_$r.weights.npz" ] || { echo "FATAL: no float cs12g050_$r"; exit 1; }
  done
  train cs12g050q_g_a035_r160 mobilenetv2 0.35 160 16 0 0 3000 3 8 \
    --qat selective --lr 1e-4 --patience 4 --init "$OUT/cloud_cs12g050_g_a035_r160.weights.npz" || exit 1
  train cs12g050q_g_a14_r224  mobilenetv2 1.4  224 8  0 0 3000 2 8 \
    --qat selective --lr 1e-4 --patience 4 --init "$OUT/cloud_cs12g050_g_a14_r224.weights.npz" || exit 1 ;;
*) echo "unknown stage $stage"; exit 1 ;;
esac; done

echo "=== summary (training-time val IoU; the number that decides comes from"
echo "    cs12_validate.py on the PTQ export, on roba-desktop) ==="
for f in "$LOGS"/cs12g050*_*.log; do
  [ -f "$f" ] && printf "  %-26s %s\n" "$(basename "$f" .log)" \
    "$(grep -aoE 'best val_iou=[0-9.]+' "$f" | tail -1)"
done
echo "next, on roba-desktop:  tools/phase2_seg_export.sh"
