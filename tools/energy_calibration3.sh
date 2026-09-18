#!/bin/bash
# Energy calibration, third pass + the static pipelines at n=50.
#
# Why a third pass. energy_calibration2.sh's rep 1 straddled the idle-floor
# transition (3.08-3.30 W for ~20 min after boot, then 2.76 W settled), so the
# derived fixed-stage and low-rung terms have one clean rep; rep 2's last run
# (segmentation high) hung the board. This re-runs the whole chain once more
# after a 25 min settle, so every derived number has two settled reps, then
# runs the static pipelines 2/6/3 at n=50 with a per-run floor for the direct
# per-frame energies. A hard hang (bench exit 3, or any failure) is met with a
# 60 s power cut, a 25 min settle, and one retry of that run.
set -u
cd "$(dirname "$0")/.." || exit 1
N="${N:-50}"; IDLE="${IDLE:-60}"; SETTLE="${SETTLE:-1500}"
IMG="${IMG:-cs12_validation_00.bayerRG}"
D=analysis/stage4.5-models/energy_cal2; mkdir -p "$D"

recover() {
  echo "=== recovering: 60 s power cut ==="
  ./tools/otii_power.py cut 60 || exit 1
  sleep 90
  for i in 1 2 3 4 5 6; do
    ./tools/board_console.py "uptime" 2>/dev/null | grep -q "load average" && break
    sleep 20
  done
  ./tools/board_console.py "mount -o ro /dev/sda1 /mnt/flash 2>/dev/null; ls /mnt/flash/cs12-frames | head -n 2"
}
try() {  # runs "$@" and on failure recovers and retries once
  "$@" && return 0
  echo "!!! failed: $*"; recover
  "$@" || { echo "!!! failed twice: $*"; exit 1; }
}
run() {  # name kind model-or-empty
  local name=$1 kind=$2 model=$3
  if [ -z "$model" ]; then
    try ./tools/bench_models.py --kind "$kind" --idle "$IDLE" --baseline --models --n "$N" --min-drain 60 --image "$IMG" -o "$D/$name.json"
  else
    try ./tools/bench_models.py --kind "$kind" --idle "$IDLE" --n "$N" --min-drain 60 --image "$IMG" --models "$model" -o "$D/$name.json"
  fi
}
r=3
run "seg_low_keepnone_r$r"  segmentation  "cs12g050_p_w08d3_r096:1:1.0"
run "seg_low_keepall_r$r"   segmentation  "cs12g050_p_w08d3_r096:1:0.0"
run "baseline_r$r"          classification ""
run "cls_low_r$r"           classification "landcover_ms_low:10"
run "cls_medop_r$r"         classification "landcover_ms_medop:10"
run "cls_high_r$r"          classification "landcover_ms_high:10"
run "seg_med_keepall_r$r"   segmentation  "cs12g050q_g_a035_r160:1:0.0"
run "seg_high_keepall_r$r"  segmentation  "cs12g050q_g_a14_r224:1:0.0"
run "seg_high_keepall_r2"   segmentation  "cs12g050q_g_a14_r224:1:0.0"
echo "=== static pipelines 2/6/3 at n=$N, per-run floor ==="
try ./tools/phase4_e2e.py --pipelines 2 6 3 --n "$N" --idle "$IDLE" --drain 90 --tag n50 \
    --images "$IMG" -o analysis/stage4.5-models/e2e_static50.json
echo "=== ALL DONE ==="
