#!/bin/bash
# Does radiometric calibration change what the segmenter sees on the device?
#
# On koge-coast-full-clouds the segmenter masks 0.54% of pixels, near-constant
# across all 99 tiles -- a cloud detector that is not tracking cloud. The module
# comment blames exactly this on the legacy NORM_MINMAX path ("that is what put
# all 99 tiles of the full-cloud frame into a single class"), so the first
# question is whether the device is on that path. It should not be:
# radiometric_mode defaults to 1, and the deployed libdemosaic.so does contain
# the parameter. This measures rather than assumes, by running both branches.
#
# It also stops the flight config depending on a module's compiled-in default
# for something this consequential.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
CFG=experiments/board/flight-config/module_1.yaml
IMG="${IMG:-koge-coast-full-clouds.bayerRG}"
OUT=analysis/stage4.5-models/dev_radiometric_ab.json
cp "$CFG" "$CFG.bak"
restore() { mv -f "$CFG.bak" "$CFG"; }
trap restore EXIT

for mode in 0 1; do
    cat > "$CFG" <<YAML
latency_cost: 373000
energy_cost: 718
parameters:
- key: normalization_value
  type: 3
  value: 255
- key: radiometric_mode
  type: 3
  value: $mode
YAML
    ./tools/bench_models.py --kind segjxl --n 1 --min-drain 120 \
        --image "$IMG" --models "seg_low:0.5:5" \
        -o "analysis/stage4.5-models/dev_radio_${mode}.json"
    # The module log is cleared per run, so read the masking now.
    ./tools/board_console.py "L=\$(ls -t /home/root/logs/tflite_segmentation_* | head -n 1); grep -ao 'Kept pixels: [0-9]* out of [0-9]*' \$L | awk '{print 150528-\$3}' | sort -n | awk -v m=$mode '{a[NR]=\$1; s+=\$1} END{printf \"MODE%s tiles=%d min=%d median=%d max=%d mean_pct=%.2f\\n\", m, NR, a[1], a[int(NR/2)], a[NR], 100*s/NR/150528}'"
done
echo "=== ALL DONE ==="
