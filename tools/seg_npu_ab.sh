#!/bin/bash
# Is the paper's original U-Net architecture NPU-accelerated where ours is not?
#
# Ours is not: turning the delegate off changes seg_low by 0.5% (48.77 ->
# 48.52 ms/tile), while the same delegate gives classification a 9.2x speedup.
# The host op dump says why -- the paper's models compile to ONE delegate
# partition, ours to four or six, because ours carry bare TRANSPOSE ops and
# extra QUANTIZE nodes that the originals do not. TRANSPOSE_CONV is not the
# culprit; the originals use it too.
#
# The host dump reflects XNNPACK partitioning, not the VX delegate, so it is
# suggestive and not proof. This is the proof: run the originals on the board
# with the delegate on and off. A large gap means the architecture is
# NPU-friendly and ours regressed; no gap means the delegate never accelerated
# segmentation and the paper's numbers came from CPU too.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
OUT=analysis/stage4.5-models/dev_seg_npu_ab.json

for spec in "custom_tiny_conv_unet:1" "custom_tiny_conv_unet:0" \
            "mobilenetv2_14_unet:1"  "mobilenetv2_14_unet:0"; do
    ./tools/bench_models.py --kind segmentation --n 1 --min-drain 180 \
        --models "$spec" -o "$OUT"
done
echo "=== ALL DONE ==="
