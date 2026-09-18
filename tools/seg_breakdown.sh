#!/bin/bash
# Where does segmentation's 48 ms/tile actually go?
#
# It costs 3x the largest classifier per tile while being 1/20th the size, and
# it is 50-64% of the pipeline under realistic retention, so its ladder is what
# pins the whole system near 2.5x. If most of that 48 ms is the fixed
# post-processing both rungs pay -- the 5x5 Gaussian over the whole tile plus the
# per-pixel mask loop -- then the variable (model) part is small, which explains
# the narrow ladder AND points at the fix.
#
# Isolation by differencing, using parameters that already exist:
#   mask_lowpass=5 : blur computed over the whole tile, then applied to masked px
#   mask_lowpass=0 : no blur at all, masked pixels set to 0
# The difference is the Gaussian. Everything else is identical, same model, same
# scene, same tile count.
#
# use_npu=0 then gives the CPU/NPU split for the inference itself, which
# separates "the model is slow" from "the delegate is slow".
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
IMG="${IMG:-koge-coast-full-clouds.bayerRG}"
OUT=analysis/stage4.5-models/dev_seg_breakdown.json

# model:pixel_threshold:mask_lowpass  (segmentation kind: model:use_npu)
echo "=== blur on (baseline) ==="
./tools/bench_models.py --kind segenc --n 1 --min-drain 120 --image "$IMG" \
    --models "seg_low:0.5:5" -o "$OUT"
echo "=== blur off ==="
./tools/bench_models.py --kind segenc --n 1 --min-drain 120 --image "$IMG" \
    --models "seg_low:0.5:0" -o "$OUT"
echo "=== NPU off, blur on (inference on A53) ==="
./tools/bench_models.py --kind segmentation --n 1 --min-drain 180 --image "$IMG" \
    --models "seg_low:0" -o "$OUT"
echo "=== ALL DONE ==="
