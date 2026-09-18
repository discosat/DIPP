#!/bin/bash
# Phase 1, board side: latency and energy of the three EuroSAT-MS classifiers.
#
# The architectures are unchanged, so the cost SHOULD carry over from the
# previous exports (173 / 676 / 1567 ms) -- this measures it rather than
# assuming it. Same method as cls_energy_n50.sh: idle floor for this session,
# baseline (demosaic + tile) at n=50, each model at n=50, per-execution cost is
# the difference over n. Never touch /dev/ttyACM0 (the Otii).
set -u
cd "$(dirname "$0")/.." || exit 1
N="${N:-50}"
OUT=analysis/stage4.5-models/dev_cls_ms.json
SRC=training/out

for r in low medop high; do
  f=$SRC/landcover_ms_${r}_qat.tflite
  [ -f "$f" ] || { echo "FATAL: missing $f"; exit 1; }
  echo "=== push $f -> /usr/share/models/landcover_ms_$r.tflite ==="
  ./tools/board_push.py "$f" "/usr/share/models/landcover_ms_$r.tflite" | tail -n 1 || exit 1
done

./tools/bench_models.py --kind classification --idle 60 --n 1 -o "$OUT" || exit 1
./tools/bench_models.py --kind classification --baseline --models --n "$N" \
    --min-drain 90 -o "$OUT" || exit 1
for r in low medop high; do
  ./tools/bench_models.py --kind classification --n "$N" --min-drain 90 \
      --models "landcover_ms_$r" -o "$OUT" || exit 1
done
echo "=== ALL DONE ==="
