#!/bin/bash
# Phase 3, board side: latency and idle-corrected energy of encoder configs,
# both codecs. Each config runs N batches in one daemon launch (energy per
# execution = E_work/N, as for the model rungs) and REP independent launches
# (spread between launches -> the +- on the final rungs).
#
#   J2K="49.8:1:1:6 149.3:1:3:6" JXL="1:3.1 5:2.4" tools/phase3_enc_board.sh
#   REP=3 SKIP_BASE=1 J2K=... JXL=... tools/phase3_enc_board.sh   # repeats
#
# J2K configs are ratio:resync:layers:resolutions (the module's own keys);
# JXL configs are effort:distance. The distances/ratios come from the corpus
# sweep (analysis/encoder_corpus_eval.py) bisected to the chosen byte budget,
# so each config here IS the deployable slot config -- the board then encodes
# the koge frame with it and reports bytes per tile, which cross-checks host
# codec against board codec on real content. Quality is the corpus's; this
# only measures cost. Never touch /dev/ttyACM0 (the Otii).
set -u
cd "$(dirname "$0")/.." || exit 1
N="${N:-4}"
REP="${REP:-1}"
OUT=${OUT:-analysis/stage4.5-models/dev_enc_g050.json}
# A CLEAR frame: the encoder only ever sees retained (majority-clear) tiles,
# and the full-cloud frame saturates to flat white at gain 0.050 -- JXL then
# emits ~260 B/tile against 3,000 on the corpus, i.e. the wrong workload.
IMG="${IMG:-cs12_validation_00.bayerRG}"
# run.sh stops when the completion count has been stable for DRAIN seconds,
# so DRAIN must exceed one batch's encode time: e8 is ~100 s/frame, e9 ~130.
DRAIN="${DRAIN:-160}"
J2K="${J2K:-}"; JXL="${JXL:-}"
[ -z "$J2K$JXL" ] && { echo "FATAL: set J2K= and/or JXL= config lists"; exit 1; }

if [ -z "${SKIP_BASE:-}" ]; then
  echo "=== idle floor (this session) ==="
  ./tools/bench_models.py --kind encoding --idle 60 --n 1 -o "$OUT" || exit 1
  echo "=== baseline: demosaic + tile, n=$N ==="
  ./tools/bench_models.py --kind encoding --baseline --models --n "$N" \
      --min-drain 60 --image "$IMG" -o "$OUT" || exit 1
fi
for r in $(seq 1 "$REP"); do
  o=$OUT
  if [ "$r" -gt 1 ]; then o=${OUT%.json}_r$r.json; [ -f "$o" ] || cp "$OUT" "$o"; fi
  for c in $J2K; do
    echo "=== j2k $c (rep $r/$REP) ==="
    ./tools/bench_models.py --kind j2k --n "$N" --min-drain "$DRAIN" --image "$IMG" \
        --models "$c" -o "$o" || exit 1
  done
  for c in $JXL; do
    echo "=== jxl $c (rep $r/$REP) ==="
    ./tools/bench_models.py --kind encoding --n "$N" --min-drain "$DRAIN" --image "$IMG" \
        --models "$c" -o "$o" || exit 1
  done
done
echo "=== ALL DONE ==="
./analysis/enc_board_summary.py "$OUT" --n "$N" --rep "$REP"
