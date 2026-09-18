#!/bin/bash
# Remaining steps after the seg-high rerun: static pipelines at n=50 (per-run
# floor before and after), then the encoder rungs at n=20.
set -u
cd "$(dirname "$0")/.." || exit 1
N=50; IDLE=60; IMG=cs12_validation_00.bayerRG
recover() {
  echo "=== recovering: 60 s power cut ==="
  ./tools/otii_power.py cut 60 || exit 1; sleep 90
  for i in 1 2 3 4 5 6; do ./tools/board_console.py "uptime" 2>/dev/null | grep -q "load average" && break; sleep 20; done
}
try() { "$@" && return 0; echo "!!! failed: $*"; recover; "$@" || { echo "!!! failed twice: $*"; exit 1; }; }
if [ ! -s analysis/stage4.5-models/energy_cal2/seg_high_keepall_r2.json ] || \
   ! python3 -c "import json,sys; d=json.load(open('analysis/stage4.5-models/energy_cal2/seg_high_keepall_r2.json')); sys.exit(0 if len([v for k,v in d.items() if k!='idle'][0]['classify_us'])>40 else 1)"; then
  echo "=== seg high rep 2 incomplete: rerunning ==="
  try ./tools/bench_models.py --kind segmentation --idle $IDLE --idle-after $IDLE --n $N --min-drain 60 --image $IMG \
      --models "cs12g050q_g_a14_r224:1:0.0" -o analysis/stage4.5-models/energy_cal2/seg_high_keepall_r2.json
fi
echo "=== static pipelines 2/6/3 at n=$N ==="
try ./tools/phase4_e2e.py --pipelines 2 6 3 --n $N --idle $IDLE --drain 90 --tag n50 \
    --images $IMG -o analysis/stage4.5-models/e2e_static50.json
echo "=== encoders n=20 ==="
./tools/phase3_enc_energy.sh
echo "=== ALL DONE ==="
