#!/bin/bash
# 2026-11-03: power-cut re-run on the final build (s5a-final6, already deployed and
# md5-checked against staging/), decided by the user after the 90 earlier cuts were
# found to predate daemon commit 9cd72b0 (energy-halt path). Final-policy scheduler
# environment exactly as tests/board/run.sh sets it for the board grid; HYBRID
# storage; adaptive pipeline 1; clear-sky frames (every tile reaches every stage)
# so that work stays queued across the cuts.
#   A  yields:       DISCO-2 BOL power, 2x load (3.34 s), every third frame urgent
#   B  energy halts: dawn-dusk 3.7 W platform, start 0.05 Wh above the floor
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

echo "== $(date) A: yields (disco2-bol, 2x clear sky, urgent every third frame)"
python3 -u tools/c3_powercut.py --storage HYBRID --pipeline 1 --image "$IMG" \
    --platform disco2-bol.env --soc0 0.9338 --phase0 0.1202 --sched "$SCHED" \
    --slo-pattern 300,300,20 --feed-gap 3.34 --batches 60 --trials 12 --settle 15 \
    --time-recovery --drain-idle 120 2>&1 | tee $OUT/powercut_final_yield.txt

echo "== $(date) B: energy halts (dawndusk-3w7, start 0.05 Wh above the floor)"
python3 -u tools/c3_powercut.py --storage HYBRID --pipeline 1 --image "$IMG" \
    --platform dawndusk-3w7.env --soc0 0.45245 --phase0 0.1202 --sched "$SCHED" \
    --slo-pattern 300,300,20 --feed-gap 6.68 --batches 30 --trials 12 --settle 20 \
    --time-recovery --drain-idle 900 2>&1 | tee $OUT/powercut_final_halt.txt

echo "== powercut-final chain done $(date)"
