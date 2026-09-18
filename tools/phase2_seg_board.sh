#!/bin/bash
# Phase 2, board side: warm latency and idle-corrected energy of the gain-0.050
# segmentation rungs, n=50 each, same method as the classification rungs.
#
#   tools/phase2_seg_board.sh                 # all ptq_*.tflite in cs12_rungs_g050
#   RUNGS="cs12g050_p_w08d3_r096" tools/phase2_seg_board.sh
#
# A first run of a model pays the VX graph compile (~13 s for the big ones), so
# each rung gets one cold n=1 run that is discarded, then the n=50 measurement.
# Baseline (demosaic + tile) is at the same n so the difference over n is one
# execution's work. Never touch /dev/ttyACM0 (the Otii).
set -u
cd "$(dirname "$0")/.." || exit 1
N="${N:-50}"
SRC=${SRC:-analysis/stage4.5-models/cs12_rungs_g050}
OUT=${OUT:-analysis/stage4.5-models/dev_cs12_g050.json}
IMG="${IMG:-cs12_validation_00.bayerRG}"
RUNGS="${RUNGS:-$(cd "$SRC" 2>/dev/null && ls ptq_*.tflite 2>/dev/null | sed 's/^ptq_//;s/\.tflite$//')}"
[ -z "$RUNGS" ] && { echo "FATAL: no ptq_*.tflite in $SRC; run tools/phase2_seg_export.sh"; exit 1; }
echo "rungs: $RUNGS"

for r in $RUNGS; do
  ./tools/board_push.py "$SRC/ptq_$r.tflite" "/usr/share/models/$r.tflite" | tail -n 1 || exit 1
done

if [ -z "${SKIP_BASE:-}" ]; then   # SKIP_BASE=1: idle+baseline already in $OUT this session
echo "=== idle floor (this session) ==="
./tools/bench_models.py --kind segmentation --idle 60 --n 1 -o "$OUT" || exit 1
echo "=== baseline: demosaic + tile, n=$N ==="
./tools/bench_models.py --kind segmentation --baseline --models --n "$N" \
    --min-drain 90 --image "$IMG" -o "$OUT" || exit 1
fi
for r in $RUNGS; do
  echo "=== $r: cold (compile, discarded) then warm n=$N ==="
  ./tools/bench_models.py --kind segmentation --n 1 --min-drain 60 --image "$IMG" \
      --models "$r:1" -o "analysis/stage4.5-models/dev_cs12_g050_${r}_cold.json" || exit 1
  ./tools/bench_models.py --kind segmentation --n "$N" --min-drain 120 --image "$IMG" \
      --models "$r:1" -o "$OUT" || exit 1
done
echo "=== ALL DONE ==="
python3 - "$OUT" "$N" <<'EOF'
import json, sys
N = int(sys.argv[2]); d = json.load(open(sys.argv[1])); idle = d["idle"]["energy"]["avg_power_w"]; b = d["baseline"]
def work(v):
    er, tr = v["energy"]["energy_wh"], v["energy"]["measured_s"]
    eb, tb = b["energy"]["energy_wh"], b["energy"]["measured_s"]
    return (er - idle*tr/3600) - (eb - idle*tb/3600)
print(f"idle {idle:.4f} W")
for k, v in d.items():
    if k in ("idle", "baseline"): continue
    lat = sorted(v["classify_us"]); n = len(lat)
    print(f"  {k:32} n={n:2} EXEC median {lat[n//2]/1000:8.1f} ms  module {v.get('module_span_ms') or float('nan'):8.1f} ms  "
          f"{work(v)*1e6/N:7.1f} uWh/exec (over N={N}, as for classification)")
EOF
