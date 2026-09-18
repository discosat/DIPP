#!/bin/bash
# Energy accounting, done right: the upload I/O term, the fixed stages, the
# low rungs with matched I/O, and the static pipelines measured directly.
#
# Why. Every rung energy so far was E(model run) - E(baseline run), and the
# baseline (demosaic + tile, no model) uploads all 99 raw tiles -- 15 MB per
# batch -- while model runs upload far less. The difference carried a
# negative I/O term that was negligible against the big rungs and dominant
# against the low ones (they measured negative). And demosaic + tile were
# never measured: 982 uWh was the old config hint, physically impossible.
#
#   1. idle floor, 120 s
#   2. seg_low thr=1.0 (0 uploads) vs thr=0.0 (99 raw uploads, 15 MB), same
#      compute -> the I/O cost of 15 MB
#   3. baseline (demosaic + tile + 15 MB upload) -> fixed stages = baseline - I/O
#   4. classification low/med/high with class_index 10 (0 uploads)
#      -> cls = run - fixed stages
#   5. static pipelines 2 / 6 / 3 at n=50 with their real (~30 kB) uploads
#      -> per-frame pipeline energy, measured, not composed
# REP launches each. Frame: cs12_validation_00. Never touch /dev/ttyACM0.
set -u
cd "$(dirname "$0")/.." || exit 1
N="${N:-50}"; REP="${REP:-2}"
IMG="${IMG:-cs12_validation_00.bayerRG}"
OUT=analysis/stage4.5-models/dev_energy_cal.json
./tools/bench_models.py --kind classification --idle 120 --n 1 -o "$OUT" || exit 1
for r in $(seq 1 "$REP"); do
  o=$OUT; [ "$r" -gt 1 ] && o=${OUT%.json}_r$r.json && { [ -f "$o" ] || cp "$OUT" "$o"; }
  echo "=== rep $r: seg_low keep-none (0 uploads) ==="
  ./tools/bench_models.py --kind segmentation --n "$N" --min-drain 60 --image "$IMG" --models "cs12g050_p_w08d3_r096:1:1.0" -o "$o" || exit 1
  echo "=== rep $r: seg_low keep-all (99 raw uploads) ==="
  ./tools/bench_models.py --kind segmentation --n "$N" --min-drain 60 --image "$IMG" --models "cs12g050_p_w08d3_r096:1:0.0" -o "$o" || exit 1
  echo "=== rep $r: baseline demosaic+tile (99 raw uploads) ==="
  ./tools/bench_models.py --kind classification --baseline --models --n "$N" --min-drain 60 --image "$IMG" -o "$o" || exit 1
  for m in low medop high; do
    echo "=== rep $r: classification $m, class 10 (0 uploads) ==="
    ./tools/bench_models.py --kind classification --n "$N" --min-drain 60 --image "$IMG" --models "landcover_ms_$m:10" -o "$o" || exit 1
  done
  echo "=== rep $r: static pipelines 2 6 3 at n=$N (real uploads) ==="
  ./tools/phase4_e2e.py --pipelines 2 6 3 --n "$N" --images "$IMG" --drain 60 -o "${o%.json}_pipelines.json" || exit 1
done
echo "=== ALL DONE ==="
