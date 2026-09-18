#!/bin/bash
# Does the 5x5 low-pass mask fill actually compress better than blacking out?
# Measured on the device, on a CLOUDY frame, with a quality-targeted encoder.
#
# Three things all have to be right for this measurement to mean anything, and
# the first attempt got two of them wrong:
#
#   Scene. On amager-no-clouds the segmenter masks 213 of 150,528 pixels
#   (0.14%), so whatever the fill does is below the noise. koge-coast-full-clouds
#   is the frame where the fill has something to fill.
#
#   Encoder. JPEG 2000 at a fixed compression_ratio TARGETS a byte budget: every
#   config came back at ~3010 B regardless of content, because that is what
#   rate-targeting means. Content changes quality, not size. JPEG XL at a fixed
#   Butteraugli distance holds QUALITY instead, so the bytes are free to move --
#   and the bytes are the claim.
#
#   Control. mask_lowpass=0 (black fill) against mask_lowpass=5, everything else
#   identical. The host measured -28% for JPEG 2000 and -5% for JPEG XL, so the
#   expected effect here is the smaller one.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
IMG="${IMG:-koge-coast-full-clouds.bayerRG}"
OUT=analysis/stage4.5-models/dev_mask_fill.json

for spec in "seg_low:0.5:0" "seg_low:0.5:5"; do
    ./tools/bench_models.py --kind segjxl --n 1 --min-drain 120 \
        --image "$IMG" --models "$spec" -o "$OUT"
done
echo "=== ALL DONE ==="
