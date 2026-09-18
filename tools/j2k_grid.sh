#!/bin/bash
# JPEG 2000 device grid: resolutions x quality_layers x resync, each at its own
# iso-rate (~3000 B/tile) so latency and energy are the only free variables.
#
# Every config here failed identically before 2026-09-11 with MODULE_EXIT_CRASH
# (600201). That error decodes as pipeline 2, module index 0 -- libdemosaic, NOT
# the encoder -- because err_current_module is i+1. The board had 61 leaked SysV
# segments holding 1.59 GB of 1.95 GB, so the first module to claim a
# full-resolution frame died. bench_models.py now runs ./shmclean before each
# config. The codec was never at fault: all 16 configs below encode 99 tiles
# clean under AddressSanitizer on the host.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
N="${N:-1}"
DRAIN="${DRAIN:-120}"
OUT=analysis/stage4.5-models/dev_grid_j2k.json

for spec in "49.03:0:1:1"  "49.03:1:1:1" \
            "246.47:0:5:1" "246.47:1:5:1" \
            "49.81:0:1:2"  "49.81:1:1:2" \
            "245.70:0:5:2" "245.70:1:5:2" \
            "46.70:0:1:3"  "46.70:1:1:3" \
            "230.93:0:5:3" "230.93:1:5:3" \
            "49.81:0:1:6"  "49.81:1:1:6" \
            "247.25:0:5:6" "247.25:1:5:6"; do
    ./tools/bench_models.py --kind j2k --n "$N" --min-drain "$DRAIN" \
        --models "$spec" -o "$OUT"
done
echo "=== done ==="
