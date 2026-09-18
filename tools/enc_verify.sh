#!/bin/bash
# Fresh baseline + the two chosen JPEG XL rungs, re-measured after the SHM
# reclaim.
#
# Why re-measure something already measured. The JXL grid ran while the board
# was accumulating the leaked segments that eventually wedged it, and its
# baseline drew 0.04895 Wh where post-reclaim J2K runs draw 0.04420 Wh -- a 10%
# gap in the run-level floor that both codecs are differenced against. If that
# gap is memory pressure rather than noise, every JXL energy figure is inflated
# and the two codecs are not comparable. Cheap to settle, expensive to get
# wrong.
#
# The baseline (demosaic+tile, no encoder) is what per-module energy is
# differenced from, so it must come from the same machine state as the rungs.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
N="${N:-1}"
DRAIN="${DRAIN:-120}"
OUT=analysis/stage4.5-models/dev_enc_verify.json

echo "=== baseline: demosaic+tile only ==="
./tools/bench_models.py --kind encoding --baseline --models --n "$N" \
    --min-drain 60 -o "$OUT"

echo "=== JPEG XL chosen rungs: effort:distance ==="
for spec in "3:1.9" "5:2.4"; do
    ./tools/bench_models.py --kind encoding --n "$N" --min-drain "$DRAIN" \
        --models "$spec" -o "$OUT"
done
echo "=== done ==="
