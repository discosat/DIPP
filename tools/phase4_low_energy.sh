#!/bin/bash
# The low rungs' energy as a MEASUREMENT, not an estimate.
#
# At n=50 the low rungs (classification 498 ms, segmentation 566 ms per frame)
# put ~2.4 mWh of work inside a 155 s run whose idle floor is known to ~1%,
# i.e. +-0.4 mWh -- they measured -92 and -71 uWh/exec. At n=200 the work is
# ~9.4 mWh against ~0.8 mWh of floor uncertainty over the extra ~100 s, so the
# difference against a baseline at the SAME n resolves. Three independent
# launches each for the spread; two 120 s idle readings for the floor.
set -u
cd "$(dirname "$0")/.." || exit 1
N="${N:-200}"; REP="${REP:-3}"
IMG="${IMG:-cs12_validation_00.bayerRG}"
OUT=analysis/stage4.5-models/dev_low_energy.json

./tools/bench_models.py --kind classification --idle 120 --n 1 -o "$OUT" || exit 1
for r in $(seq 1 "$REP"); do
  o=$OUT; [ "$r" -gt 1 ] && o=${OUT%.json}_r$r.json && { [ -f "$o" ] || cp "$OUT" "$o"; }
  echo "=== rep $r/$REP: baseline (demosaic+tile) n=$N ==="
  ./tools/bench_models.py --kind classification --baseline --models --n "$N" --min-drain 60 --image "$IMG" -o "$o" || exit 1
  echo "=== rep $r/$REP: classification low n=$N ==="
  ./tools/bench_models.py --kind classification --n "$N" --min-drain 60 --image "$IMG" --models landcover_ms_low -o "$o" || exit 1
  echo "=== rep $r/$REP: segmentation low n=$N ==="
  ./tools/bench_models.py --kind segmentation --n "$N" --min-drain 60 --image "$IMG" --models cs12g050_p_w08d3_r096:1 -o "$o" || exit 1
done
./tools/bench_models.py --kind classification --idle 120 --n 1 -o "${OUT%.json}_idle2.json" || exit 1
echo "=== ALL DONE ==="
