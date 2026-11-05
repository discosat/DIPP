#!/bin/bash
# 2026-11-05: session C of the final-build power cuts (see
# analysis/stage5/persistence/powercut_final_README.md). Sessions A/B never hit an
# energy halt or a resume from a checkpoint. Here the deficit platform starts ~3 mWh
# above the floor (floor = 2 x 13.07 W x 95.43 min = 41.5753 Wh; soc0 0.45194 ->
# 41.578 Wh), about one frame of energy, so batches halt mid-pipeline on every boot
# and recharge at the ~0.4 W surplus. Same build, policy environment and frames as A/B.
set -u
cd /home/roba/dipp-paper/DIPP
export BOARD_TTY=/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0
[ -e "$BOARD_TTY" ] || { echo "board console $BOARD_TTY missing"; exit 1; }
if ps -eo args | grep -E "campaign.py|board_console|board-grid-chain|board_push|c3_powercut" | grep -v grep; then
    echo "== a campaign or console process is running; not starting"; exit 1
fi
OUT=analysis/stage5/persistence
IMG=/mnt/flash/cs12-frames/cs12_test_matchedkeep_00.bayerRG
SCHED="DIPP_SCHED_Q_MED=auto DIPP_SCHED_Q_LOW=auto DIPP_SCHED_GRANULARITY=stage DIPP_SCHED_GATE_MED_MS=5000 DIPP_SCHED_GATE_LOW_MS=3000 DIPP_SCHED_WINDOW=8 DIPP_SCHED_FRAME_OVERHEAD_MS=780 DIPP_SCHED_GUARD=on DIPP_SCHED_ACCEPT=feas DIPP_SCHED_K_X10=20 DIPP_SCHED_SMAX_HIGH_MS=12150 DIPP_SCHED_SMAX_MED_MS=7330 DIPP_SCHED_ENERGY=on"

echo "== $(date) C: energy halts (dawndusk-3w7, start ~3 mWh above the floor)"
python3 -u tools/c3_powercut.py --storage HYBRID --pipeline 1 --image "$IMG" \
    --platform dawndusk-3w7.env --soc0 0.45194 --phase0 0.1202 --sched "$SCHED" \
    --slo-pattern 300,300,20 --feed-gap 6.68 --batches 40 --trials 12 --settle 15 \
    --time-recovery --drain-idle 900 2>&1 | tee $OUT/powercut_final_energy.txt
echo "== powercut-final C done $(date)"
