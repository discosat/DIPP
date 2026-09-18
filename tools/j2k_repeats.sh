#!/bin/bash
# Idle floor, then the three chosen J2K rungs repeated, to separate real energy
# differences from measurement noise.
#
# Why this is needed. The J2K rungs span 2.3x in latency but only ~0.6 mWh in
# run energy, and the resync-on/off pairs -- which do the same work and should
# read the same -- differ by 0.4-0.6 mWh. That is the noise floor sitting at the
# size of the entire effect, so with n=1 the energy ordering of these rungs is
# not established. Either repeats separate them or the honest claim is that
# JPEG 2000 rungs are distinguishable in time and not in energy.
#
# Idle first, on a reclaimed machine with no daemon: run lengths differ by tens
# of seconds (56 s to 81 s), and at ~2.5 W board floor those seconds are worth
# more than the module energies being measured.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
OUT=analysis/stage4.5-models/dev_j2k_repeats.json

./tools/bench_models.py --kind j2k --idle 60 --n 1 -o "$OUT"
./tools/bench_models.py --kind j2k --baseline --models --n 1 --min-drain 120 -o "$OUT"

for rep in 1 2 3; do
  for spec in "49.81:1:1:6" "149.31:1:3:6" "250.36:1:5:6"; do
    # bench_models keys results by config, so a repeat would overwrite. Write
    # each repetition to its own file and merge in analysis.
    ./tools/bench_models.py --kind j2k --n 1 --min-drain 120 \
        --models "$spec" -o "analysis/stage4.5-models/dev_j2k_rep${rep}.json"
  done
done
echo "=== ALL DONE ==="
