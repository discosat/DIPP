#!/bin/bash
# Measured latency_cost / energy_cost for every scheduled stage.
#
# Every cost hint in the committed flight config is a placeholder, and the
# scheduler's whole decision is a comparison of these two numbers against the
# time and energy it has left. The monolith's are 10 us / 5 uWh against true
# values in the hundreds of thousands, which is why pipeline 4 scheduled three
# to five orders of magnitude off in the published figures.
#
# Order matters. Idle comes FIRST, on a freshly reclaimed machine with no
# daemon, because every per-module energy figure is differenced against it:
#
#     E_work = (E_run - P_idle*T_run) - (E_base - P_idle*T_base)
#
# Then the baseline (demosaic+tile, no inference, no encoder), then each rung.
# Runs differ in length by tens of seconds, so the idle term is not a rounding
# correction -- at ~2.5 W it is larger than most of the module energies here.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
N="${N:-1}"
DRAIN="${DRAIN:-60}"
OUT=analysis/stage4.5-models/dev_cost_hints.json

echo "=== idle floor (daemon stopped) ==="
./tools/bench_models.py --kind classification --idle 60 --n "$N" -o "$OUT"

echo "=== baseline: demosaic + tile only ==="
./tools/bench_models.py --kind classification --baseline --models --n "$N" \
    --min-drain "$DRAIN" -o "$OUT"

echo "=== classification rungs (slots 3/4/5) ==="
for m in custom_conv_model_landcover qat_a050r224 qat_a140r224; do
    ./tools/bench_models.py --kind classification --n "$N" --min-drain "$DRAIN" \
        --models "$m" -o "$OUT"
done

# seg_high (qat_cloud_g_a10_r224) is omitted deliberately: the whole `g` family
# carries 15 dynamic-shape ops and fails VX delegate graph verification, so it
# has no cost to measure until it is re-exported with batch_size=1. seg_high_p
# (qat_cloud_p_w16d3_r128) is the NPU-safe candidate, measured here so the
# choice between re-exporting `g` and shipping `p` is made on numbers.
echo "=== segmentation rungs (slots 6/7) ==="
for m in seg_low seg_high_p; do
    ./tools/bench_models.py --kind segmentation --n "$N" --min-drain "$DRAIN" \
        --models "$m" -o "$OUT"
done
echo "=== done ==="
