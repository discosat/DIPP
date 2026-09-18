#!/bin/bash
# Classification energy at n=50, because at n=1 it is not measurable.
#
# A run costs ~56 s of fixed overhead whatever it does, and the board idles at
# ~3 W, so one batch of a 184 ms module is a rounding error against the floor:
# measured at n=1 the two cheap rungs came out at -41 and -134 uWh, i.e. below
# the ~100 uWh noise. Negative energy is the measurement telling you the
# workload is too small, not that inference is free.
#
# Repeating the batch 50x multiplies the work while the floor stays put. The
# BASELINE runs at the same n for the same reason -- demosaic and tile also run
# 50 times, so differencing against an n=1 baseline would charge the module for
# 49 extra demosaics. Per-execution cost is the difference divided by n.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
N="${N:-50}"
OUT=analysis/stage4.5-models/dev_cls_energy.json

./tools/bench_models.py --kind classification --idle 60 --n 1 -o "$OUT"
./tools/bench_models.py --kind classification --baseline --models --n "$N" \
    --min-drain 90 -o "$OUT"
for m in custom_conv_model_landcover qat_a050r224 qat_a140r224; do
    ./tools/bench_models.py --kind classification --n "$N" --min-drain 90 \
        --models "$m" -o "$OUT"
done
echo "=== ALL DONE ==="
