#!/bin/bash
# The JPEG 2000 rung candidates, measured on the board.
#
# resolutions is fixed at 6 because it is a dominated knob: at iso-rate it is
# both the best quality (47.6 dB vs 32.5 dB at 1 level) and the cheapest
# (42.6 vs 62.4 ms/tile on device), so no scheduler would ever spend effort to
# lower it. Quality layers are the rung: they cost real compute at flat
# full-receipt PSNR and buy degraded-mode fidelity on a lossy link.
#
# Rates come from analysis/j2k_layer_ladder.py, bisected to ~3000 B/tile on
# real device tiles with resync markers on, so bytes are held constant and
# latency/energy are the only free variables.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
N="${N:-1}"
DRAIN="${DRAIN:-120}"
OUT=analysis/stage4.5-models/dev_j2k_rungs.json

# ratio:resync:layers:resolutions
for spec in "49.81:1:1:6" "99.56:1:2:6" "149.31:1:3:6" "250.36:1:5:6" \
            "49.81:0:1:6" "250.36:0:5:6"; do
    ./tools/bench_models.py --kind j2k --n "$N" --min-drain "$DRAIN" \
        --models "$spec" -o "$OUT"
done
echo "=== done ==="
