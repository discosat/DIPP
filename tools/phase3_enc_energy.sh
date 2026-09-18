#!/bin/bash
# Encoder energy, properly: n=20 batches per config with an idle-floor
# reading immediately before each run, in the settled state (>25 min after
# boot). The Phase 3 cost session (dev_enc_cs12.json) used n=4 against a
# baseline that uploads 15 MB where the encoder uploads 0.3 MB, so its
# per-exec numbers carry ~+-60 uWh of noise and are short by the I/O term
# (~300 uWh/frame, tools/energy_calibration2.sh). Here every run has its own
# floor and the encoder is differenced against the fixed stages measured in
# the same way; the residual 0.3 MB upload is charged from the I/O term.
set -u
cd "$(dirname "$0")/.." || exit 1
N="${N:-20}"; IDLE="${IDLE:-60}"; SETTLE="${SETTLE:-0}"
IMG="${IMG:-cs12_validation_00.bayerRG}"
D=${OUTDIR:-analysis/stage4.5-models/energy_enc}; mkdir -p "$D"
recover() {
  echo "=== recovering: 60 s power cut ==="
  ./tools/otii_power.py cut 60 || exit 1; sleep 90
  for i in 1 2 3 4 5 6; do ./tools/board_console.py "uptime" 2>/dev/null | grep -q "load average" && break; sleep 20; done
}
try() { "$@" && return 0; echo "!!! failed: $*"; recover; "$@" || { echo "!!! failed twice: $*"; exit 1; }; }
sleep "$SETTLE"
# demosaic + tile + 15 MB upload, the same-session reference
try ./tools/bench_models.py --kind encoding --idle "$IDLE" --idle-after "$IDLE" --baseline --models --n "$N" --min-drain 60 --image "$IMG" -o "$D/baseline.json"
for c in 49.22:1:1:6 98.2:1:2:6 147.56:1:3:6; do
  try ./tools/bench_models.py --kind j2k --idle "$IDLE" --idle-after "$IDLE" --n "$N" --min-drain 90 --image "$IMG" --models "$c" -o "$D/j2k_${c%%:*}.json"
done
for c in 3:2.358 5:2.884; do
  try ./tools/bench_models.py --kind encoding --idle "$IDLE" --idle-after "$IDLE" --n "$N" --min-drain 160 --image "$IMG" --models "$c" -o "$D/jxl_e${c%%:*}.json"
done
echo "=== ALL DONE ==="
