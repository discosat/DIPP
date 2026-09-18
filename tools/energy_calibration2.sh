#!/bin/bash
# Energy calibration, definitive pass: an idle-floor reading immediately
# BEFORE EVERY RUN, each run in its own JSON with its own floor.
#
# Why. In the first pass the floor was read once, 3 minutes after a power
# cycle, at 3.058 W against 2.70-2.83 W in every earlier session, and it
# drifted down during the campaign: later runs looked cheaper, the medium
# classification rung came out below the low one. A 0.1 W floor error over a
# 154 s run is 86 uWh per batch -- larger than the low rungs. That is the
# plan's open unknown U2, thermal drift of the idle floor, observed. Runs are
# all n=50 and ~154 s, so run-to-run differences cancel the floor; the
# absolute numbers need the floor read right before each run.
set -u
cd "$(dirname "$0")/.." || exit 1
N="${N:-50}"; IDLE="${IDLE:-60}"
IMG="${IMG:-cs12_validation_00.bayerRG}"
D=analysis/stage4.5-models/energy_cal2; mkdir -p "$D"
run() {  # name kind model-or-empty
  local name=$1 kind=$2 model=$3
  if [ -z "$model" ]; then
    ./tools/bench_models.py --kind "$kind" --idle "$IDLE" --baseline --models --n "$N" --min-drain 60 --image "$IMG" -o "$D/$name.json" || exit 1
  else
    ./tools/bench_models.py --kind "$kind" --idle "$IDLE" --n "$N" --min-drain 60 --image "$IMG" --models "$model" -o "$D/$name.json" || exit 1
  fi
}
echo "=== settle 5 min after any reboot before measuring ==="
sleep "${SETTLE:-300}"
for r in 1 2; do
  run "seg_low_keepnone_r$r"  segmentation  "cs12g050_p_w08d3_r096:1:1.0"
  run "seg_low_keepall_r$r"   segmentation  "cs12g050_p_w08d3_r096:1:0.0"
  run "baseline_r$r"          classification ""
  run "cls_low_r$r"           classification "landcover_ms_low:10"
  run "cls_medop_r$r"         classification "landcover_ms_medop:10"
  run "cls_high_r$r"          classification "landcover_ms_high:10"
  run "seg_med_keepall_r$r"   segmentation  "cs12g050q_g_a035_r160:1:0.0"
  run "seg_high_keepall_r$r"  segmentation  "cs12g050q_g_a14_r224:1:0.0"
done
echo "=== ALL DONE ==="
