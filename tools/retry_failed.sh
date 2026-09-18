#!/bin/bash
# Re-run the two configs that failed in the cost-hints sweep, to separate a
# reproducible defect from a transient. Both failed with completed=0 after the
# full drain; qat_a050r224 crashed the module process (600203) while seg_high
# failed VX delegate graph verification (704203) at node 106. Those are
# different failure modes, and a140 -- structurally identical to a050 -- worked,
# so neither is explained yet.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
for rep in 1 2; do
  ./tools/bench_models.py --kind classification --n 1 --min-drain 120 \
      --models qat_a050r224 -o "analysis/stage4.5-models/retry_cls_${rep}.json"
  ./tools/bench_models.py --kind segmentation --n 1 --min-drain 120 \
      --models seg_high -o "analysis/stage4.5-models/retry_seg_${rep}.json"
done
echo "=== ALL DONE ==="
