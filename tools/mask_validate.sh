#!/bin/bash
# Validate pixel_threshold and mask_lowpass ON THE DEVICE.
#
# Both were host-validated only. They are also both claims the paper leans on:
# pixel_threshold was hardcoded at 0.5 before this revision, and the 5x5
# low-pass mask fill is what makes JPEG 2000 the better codec under masking
# (-28% bytes on the host, against +130% for blacking cloud out). A claim
# measured only on x86 is not a claim about the flight system.
#
# The pipeline runs segmentation THEN the encoder, because the fill's effect is
# on compressibility and a pipeline stopping at segmentation cannot see it.
#   pixel_threshold -> "Kept pixels" in the module's own log
#   mask_lowpass    -> encoded bytes per tile
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
N="${N:-1}"
OUT=analysis/stage4.5-models/dev_mask_validate.json

# model:pixel_threshold:mask_lowpass
# Thresholds at fixed fill, then fill on/off at the default threshold. A
# threshold that is ignored shows identical kept-pixel counts across the first
# three; a fill that is ignored shows identical bytes across the last two.
for spec in "seg_low:0.3:5" "seg_low:0.5:5" "seg_low:0.7:5" \
            "seg_low:0.5:0"; do
    ./tools/bench_models.py --kind segenc --n "$N" --min-drain 120 \
        --models "$spec" -o "$OUT"
done
echo "=== ALL DONE ==="
