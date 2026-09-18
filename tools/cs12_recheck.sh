#!/bin/bash
# Re-test with the input range pinned to the module's passthrough contract.
#
# First attempt: board masked 0.00% where the host masked 96.58%, on BOTH the
# NPU and the CPU. Identical on both is what identifies it -- a delegate fault
# would differ between them. The cause was the input quantisation: the module
# writes the raw uint8 tile into the input tensor, so the model sees
# (v - zp) * scale, and only zp=128 / scale=2/255 makes that equal the
# v/127.5 - 1 the model trained on. Device-tile calibration gave zp=171 /
# scale=0.005075 because device tiles span only [-0.87, 0.43], so a pixel of
# 182 arrived as 0.056 instead of 0.427 and nothing crossed the threshold.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
OUT=analysis/stage4.5-models/dev_cs12_recheck.json
for img in koge-coast-full-clouds koge-coast-no-clouds; do
  echo "=== $img ==="
  ./tools/bench_models.py --kind segmentation --n 1 --min-drain 150 \
      --image "$img.bayerRG" --models "cs12_smoke2:1" -o "$OUT"
  ./tools/board_console.py "L=\$(ls -t /home/root/logs/tflite_segmentation_* 2>/dev/null | head -n 1); \
    grep -ao 'Kept pixels: [0-9]* out of [0-9]*' \$L | \
    awk '{m+=(150528-\$3); t+=150528} END{if(t)printf \"MASKED %s : %.2f%% over %d tiles\\n\", \"'$img'\", 100*m/t, NR}'"
done
echo "=== ALL DONE ==="
