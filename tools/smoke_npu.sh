#!/bin/bash
# Does the PTQ + batch_size=1 export actually get accelerated on the NPU?
# Untrained weights: only the delegate's behaviour is under test, not quality.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
for spec in "smoke_g:1" "smoke_g:0"; do
    ./tools/bench_models.py --kind segmentation --n 1 --min-drain 180 \
        --models "$spec" -o analysis/stage4.5-models/dev_smoke_npu.json
done
echo "=== ALL DONE ==="
