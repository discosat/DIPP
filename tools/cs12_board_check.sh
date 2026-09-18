#!/bin/bash
# Does the board reproduce the host's masks, and is the NPU used?
#
# The host, on real device tiles, gives 96.58% masked on koge-coast-full-clouds
# and 0.02-0.03% on the two cloud-free frames. If the board agrees, the export
# and the delegate are faithful and the remaining training is worth doing. If it
# does not, convert_tf29.py's documented failure applies -- a model that loads,
# allocates, runs, and returns the same answer for every input -- and no amount
# of extra training would fix it.
#
# Both frames matter. A model stuck at "everything is cloud" would also score
# 96% on the cloudy frame; only the clear frames distinguish that from working.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
OUT=analysis/stage4.5-models/dev_cs12_check.json

for img in koge-coast-full-clouds koge-coast-no-clouds; do
  for npu in 1 0; do
    echo "=== $img  use_npu=$npu ==="
    ./tools/bench_models.py --kind segmentation --n 1 --min-drain 150 \
        --image "$img.bayerRG" --models "cs12_smoke:$npu" -o "$OUT"
    ./tools/board_console.py "L=\$(ls -t /home/root/logs/tflite_segmentation_* 2>/dev/null | head -n 1); \
      grep -ao 'Kept pixels: [0-9]* out of [0-9]*' \$L | \
      awk '{m+=(150528-\$3); t+=150528} END{if(t)printf \"MASKED %s npu=%s : %.2f%% over %d tiles\\n\", \"'$img'\", \"'$npu'\", 100*m/t, NR}'"
  done
done
echo "=== ALL DONE ==="
