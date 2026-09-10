#!/bin/bash
# Full encoder grid on the board: latency and energy for every candidate rung,
# both codecs, at one fixed byte budget.
#
# Why on the board. Every encoder number so far came from an x86 host, and two
# things make those unsafe to set config from. The board runs A53 cores whose
# cache and SIMD behaviour is nothing like x86, so ratios need not carry over.
# And the JPEG 2000 host timings went through subprocess, burying ~8-10 ms of
# process startup in every measurement -- which is most of the 11-17 ms range
# that led me to call its energy span too small to schedule on. That conclusion
# may be an artefact; this settles it.
#
# Rates and distances come from analysis/encoder_grid.py, which bisected each
# config to ~3000 B/tile on real device tiles. Holding bytes constant is what
# makes latency and energy the only variables, so the scheduler is choosing
# compute-for-quality rather than compute-for-bandwidth.
#
# A baseline run (demosaic+tile, no encoder) is measured first so per-module
# energy can be differenced out of the ~48 mWh run-level floor.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"

N="${N:-1}"
DRAIN="${DRAIN:-300}"
OUT_JXL=analysis/stage4.5-models/dev_grid_jxl.json
OUT_J2K=analysis/stage4.5-models/dev_grid_j2k.json

echo "=== baseline: demosaic+tile only ==="
./tools/bench_models.py --kind encoding --baseline --models --n "$N" \
    --min-drain 60 -o "$OUT_JXL" 2>&1 | grep -E "EXEC|SKIP"

# JPEG XL: effort 1..9, each at the distance holding the byte budget.
echo "=== JPEG XL: effort sweep at iso-rate ==="
for spec in "1:1.414" "2:1.414" "3:1.277" "4:1.414" "5:1.391" \
            "6:1.505" "7:1.505" "8:1.243" "9:1.243"; do
    ./tools/bench_models.py --kind encoding --n "$N" --min-drain "$DRAIN" \
        --models "$spec" -o "$OUT_JXL" 2>&1 | grep -E "EXEC|SKIP"
done

# JPEG 2000: resolutions x layers x resync, each at its own iso-rate.
# Fewer decomposition levels code larger subbands and so cost MORE time, which
# is the opposite of the intuition and the only J2K knob that might span enough
# to schedule on.
echo "=== JPEG 2000: resolutions x layers x resync at iso-rate ==="
for spec in "49.03:0:1:1"  "49.03:1:1:1" \
            "246.47:0:5:1" "246.47:1:5:1" \
            "49.81:0:1:2"  "49.81:1:1:2" \
            "245.70:0:5:2" "245.70:1:5:2" \
            "46.70:0:1:3"  "46.70:1:1:3" \
            "230.93:0:5:3" "230.93:1:5:3" \
            "49.81:0:1:6"  "49.81:1:1:6" \
            "247.25:0:5:6" "247.25:1:5:6"; do
    ./tools/bench_models.py --kind j2k --n "$N" --min-drain "$DRAIN" \
        --models "$spec" -o "$OUT_J2K" 2>&1 | grep -E "EXEC|SKIP"
done

echo "=== done ==="
