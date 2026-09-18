#!/bin/bash
# Only what is missing: the run that hung (seg high, rep 2), then the static
# pipelines at n=50 and the encoder rungs at n=20. Each run reads its own
# floor; a hang is detected within ~3 min and met with a power cut + retry
# of THAT run only.
set -u
cd "$(dirname "$0")/.." || exit 1
N=50; IDLE=60; IMG=cs12_validation_00.bayerRG
D=analysis/stage4.5-models/energy_cal2
recover() {
  echo "=== recovering: 60 s power cut ==="
  ./tools/otii_power.py cut 60 || exit 1; sleep 90
  for i in 1 2 3 4 5 6; do ./tools/board_console.py "uptime" 2>/dev/null | grep -q "load average" && break; sleep 20; done
}
try() { "$@" && return 0; echo "!!! failed: $*"; recover; "$@" || { echo "!!! failed twice: $*"; exit 1; }; }
try ./tools/bench_models.py --kind segmentation --idle $IDLE --idle-after $IDLE --n $N --min-drain 60 --image $IMG \
    --models "cs12g050q_g_a14_r224:1:0.0" -o "$D/seg_high_keepall_r2.json"
echo "=== static pipelines 2/6/3 at n=$N ==="
try ./tools/phase4_e2e.py --pipelines 2 6 3 --n $N --idle $IDLE --drain 90 --tag n50 \
    --images $IMG -o analysis/stage4.5-models/e2e_static50.json
echo "=== encoders n=20 ==="
./tools/phase3_enc_energy.sh
echo "=== ALL DONE ==="
