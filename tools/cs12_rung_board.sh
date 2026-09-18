#!/bin/bash
# Measure the CloudSEN12 rungs on the board: WARM latency and energy.
#
# Warm matters. The VX delegate compiles the graph on first load and the module
# caches it (cache_file_path), but a cold run charges that compile to the
# measurement: the alpha=1.0 smoke model read EXEC 15,279 ms against 2,212 ms of
# module time -- a 13 s graph compile. latency_cost taken from a cold run would
# be wrong by an order of magnitude, so every config is run twice and the first
# is discarded.
#
# Idle is measured first, in this session, because the floor is not a constant:
# it has read 2.739, 3.097 and 3.290 W on different sessions, a 0.55 W spread
# larger than every module energy being measured. Energy is then
#     E_work = (E_run - P_idle*T_run) - (E_base - P_idle*T_base)
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
OUT=analysis/stage4.5-models/dev_cs12_rungs.json
SRC=analysis/stage4.5-models/cs12_rungs
IMG="${IMG:-koge-coast-full-clouds.bayerRG}"

RUNGS="${RUNGS:-$(cd "$SRC" 2>/dev/null && ls ptq_cs12_*.tflite 2>/dev/null | sed 's/^ptq_//;s/\.tflite$//')}"
[ -z "$RUNGS" ] && { echo "FATAL: no exports in $SRC; run analysis/rung_export_smoke.py"; exit 1; }
echo "rungs: $RUNGS"

echo "=== idle floor (this session) ==="
./tools/bench_models.py --kind segmentation --idle 60 --n 1 -o "$OUT"
echo "=== baseline: demosaic + tile ==="
./tools/bench_models.py --kind segmentation --baseline --models --n 1 \
    --min-drain 60 --image "$IMG" -o "$OUT"

for r in $RUNGS; do
    echo "=== $r ==="
    ./tools/board_push.py "$SRC/ptq_$r.tflite" "/usr/share/models/$r.tflite" \
        | tail -1
    # Twice: the first run pays the graph compile, the second is the real cost.
    for rep in cold warm; do
        ./tools/bench_models.py --kind segmentation --n 1 --min-drain 150 \
            --image "$IMG" --models "$r:1" \
            -o "analysis/stage4.5-models/dev_cs12_${r}_${rep}.json"
    done
done
echo "=== ALL DONE ==="
