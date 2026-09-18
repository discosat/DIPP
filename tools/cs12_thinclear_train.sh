#!/bin/bash
# Does counting thin cloud as CLEAR restore device separation?
#
# THE OBSERVATION. Every well-trained rung over-predicts cloud on cloud-free
# device frames, and it gets worse with training and with capacity, while a
# 3-epoch model does not:
#
#   rung                val_iou   cloudy   clear   separation
#   cs12_smoke (3 ep)    0.2414   96.79%   0.03%     96.76 pp
#   p_w08d3_r096         0.7309  100.00%  19.86%     80.14 pp
#   p_w16d4_r160         0.7469   99.01%  67.79%     31.21 pp
#   g_a14_r224           0.8168   50.77%  60.08%     -9.31 pp   <- inverted
#
# val_iou is ANTI-correlated with usefulness on device. All rungs passed the op
# and quantisation checks, so this is not an export fault.
#
# THE HYPOTHESIS. Thin cloud is DIMMER than clear ground in this corpus --
# measured means 1555.7 against 1711.8 in red -- so training with thin counted
# as cloud teaches "dim can be cloud". Our clear device frames are Danish coast
# and city, full of dark water. A model that has learned the thin-cloud subtlety
# flags dark water; the 3-epoch model never learned it, which is precisely why
# it still separates. More training makes the corpus task better and the
# deployment task worse.
#
# THE TEST. Same three architectures across the capacity range, thin counted as
# CLEAR. If separation recovers, the label collapse was the problem and the
# ladder is built on thick cloud. If it does not, the cause is PTQ damage or the
# domain map, and this rules out the cheapest explanation first.
#
# The cost is the ladder argument: thin cloud is what gives capacity something
# to buy, so a thick-only target may flatten the rungs again. That trade is the
# point of measuring rather than assuming.
set -u

REMOTE=roba-desktop
REPO=/home/roba/dipp-paper/DIPP
DATA=${CLOUDSEN12_DIR:-$HOME/dipp-datasets/cloudsen12}
WORK=$HOME/cs12-train
CODE=$WORK/code; OUT=$WORK/out; LOGS=$WORK/logs
SIF=${SIF:-$HOME/sipp-cloud-training/tf215.sif}
export PYTHONNOUSERSITE=1
# Apptainer on desktop1 binds /run/systemd/resolve from apptainer.conf and dies
# if that path is absent ("mount source /run/systemd/resolve doesn't exist"),
# which happens whenever systemd-resolved is not running. Disabling the
# config-file bind paths avoids it; $HOME and the GPU come through separate
# mechanisms, and this job needs no DNS -- everything it reads is a local file.
export APPTAINER_NO_MOUNT="${APPTAINER_NO_MOUNT:-bind-paths}"

mkdir -p "$CODE" "$OUT" "$LOGS" || exit 1
# The shards have to be visible from THIS machine. cs12_gpu_train.sh checks
# this; omitting it here meant a missing data path surfaced as three silent
# no-op "successes" instead of one clear error.
for f in train_rgb.npy train_label.npy validation_rgb.npy validation_label.npy; do
  [ -f "$DATA/$f" ] || { echo "FATAL: missing $DATA/$f"; echo \
    "  set CLOUDSEN12_DIR= to where pack_cloudsen12.py wrote the shards"; exit 1; }
done
echo "data: $(du -sh "$DATA" 2>/dev/null | cut -f1) at $DATA"
rsync -a "$REMOTE:$REPO/training/train_cloud.py" "$CODE/" || exit 1
grep -q "thin_is_cloud" "$CODE/train_cloud.py" || { echo "FATAL: stale train_cloud.py"; exit 1; }

#      backbone alpha res batch width depth limit crops tag
GRID="
plain        0    96  16 8  3 4000 4 cs12tc_p_w08d3_r096
plain        0    160 16 16 4 3000 3 cs12tc_p_w16d4_r160
mobilenetv2  1.0  224 8  0  0 3000 2 cs12tc_g_a10_r224
"
echo "$GRID" | while read -r spec; do
  [ -z "$spec" ] && continue
  set -- $spec
  tag=$9
  # Say WHICH file caused a skip. A bare "SKIP" is indistinguishable from a
  # tag that got mangled into an existing name, and FORCE= exists so a rerun
  # never needs a manual rm.
  w="$OUT/cloud_$tag.weights.npz"
  if [ -f "$w" ] && [ -z "${FORCE:-}" ]; then
      echo "SKIP $tag (exists: $w, $(stat -c%s "$w") B, $(stat -c%y "$w"))"
      echo "     set FORCE=1 to retrain anyway"
      continue
  fi
  echo "    target: $w"
  echo "--- $tag (thin=CLEAR) ---"
  apptainer exec --nv --env CLOUDSEN12_DIR="$DATA" --env PYTHONNOUSERSITE=1 \
    "$SIF" python "$CODE/train_cloud.py" \
      --dataset cloudsen12 --effort high --thin-clear \
      --backbone "$1" --alpha "$2" --res "$3" --batch "$4" \
      --width "$5" --depth "$6" --limit "$7" --crops "$8" \
      --loss bcedice --patience 12 --epochs 40 --dec-max 128 \
      --tag "$tag" --out "$OUT" > "$LOGS/$tag.log" 2>&1
  # Verify the run actually finished before calling it DONE. Printing DONE on
  # the strength of the loop having advanced is how a failed sweep reports
  # success: the first version of this said "DONE <tag>" with an empty val_iou
  # for three rungs that had all crashed on the same error.
  iou=$(grep -aoE 'best val_iou=[0-9.]+' "$LOGS/$tag.log" | tail -1)
  if [ -z "$iou" ] || [ ! -f "$OUT/cloud_$tag.weights.npz" ]; then
      echo "FAILED $tag -- no val_iou and/or no weights. Last lines:"
      tail -n 15 "$LOGS/$tag.log" | sed 's/^/    /'
      echo "  stopping: the rungs share a data path, so the next one fails too"
      exit 1
  fi
  echo "DONE $tag $iou"
  rsync -a "$OUT/cloud_$tag.weights.npz" "$OUT/cloud_$tag.json" \
        "$LOGS/$tag.log" "$REMOTE:$REPO/training/out/" 2>/dev/null
done
echo "=== ALL DONE ==="
for f in "$LOGS"/cs12tc_*.log; do
  printf "  %-24s %s\n" "$(basename "$f" .log)" \
    "$(grep -aoE 'best val_iou=[0-9.]+' "$f" | tail -1)"
done
