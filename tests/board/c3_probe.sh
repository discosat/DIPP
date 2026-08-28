#!/bin/sh
# Targeted probe for the residual C3 loss.
#
# The failure signature is always the same: a batch is selected from the PARTIAL
# queue at progress=3, the daemon dies during module 4, and afterwards the batch
# is in neither queue. Rather than kill at random and wait for that to happen by
# chance (~20 min per run), this kills exactly at that moment.
#
# Each trial reports the one fact that matters: the partial queue depth the next
# daemon sees at startup. depth=1 means the entry survived the kill and the loss
# is later; depth=0 means it never reached storage.
#
#   c3_probe.sh [trials]
set -u
TRIALS=${1:-10}
IMG=/mnt/flash/2025-11-10/sentinel2-dataset/amager-no-clouds.bayerRG
cd /home/root/tmp-experiments || exit 1

echo "=== C3 probe: $TRIALS trials, kill during module 4 ==="
t=1
while [ "$t" -le "$TRIALS" ]; do
    pkill -KILL dipp 2>/dev/null; sleep 1
    # Queues and data are cleared per trial, but NOT cost.cache: clearing it
    # makes every module a cold miss and the first batch then spends minutes
    # rebuilding 23 MB TFLite delegate caches, which is what made every trial
    # time out.
    rm -f /usr/share/dipp/queue_file /usr/share/dipp/partially_processed_queue_file
    find /usr/share/dipp/data -type f -exec rm -f {} + 2>/dev/null
    rm -f probe.log

    (setsid ./dipp -i CAN -p can0 -a 5423 >> probe.log 2>&1 &)
    # Feed IMMEDIATELY, with no settling time. Initial SoC is 0.7 x 92 Wh =
    # 64.4 Wh, which is exactly BATTERY_SAFETY_MARGIN_WH, so
    # energy_requirement = (battery - margin) starts at ZERO and the scheduler
    # refuses every module until the battery charges. That startup window is
    # the only time batches get parked as partial -- sleeping 2 s here let the
    # battery gain enough headroom to run all five modules in one pass, which
    # is why earlier probe attempts never produced a single partial.
    sleep 1
    b=1
    while [ "$b" -le 8 ]; do
        ./emulate_camera 1 2 123 "$IMG" > /dev/null 2>&1
        b=$((b + 1))
    done

    # Wait for the batch to reach the partial queue at progress=3 and be picked
    # up for the final module.
    w=0
    while [ "$w" -lt 120 ]; do
        if grep -q 'QUEUE_SELECT queue=partial .* progress=3' probe.log 2>/dev/null; then
            break
        fi
        sleep 1
        w=$((w + 1))
    done

    if [ "$w" -ge 120 ]; then
        echo "trial $t: TIMEOUT waiting for progress=3 in partial"
        t=$((t + 1))
        continue
    fi

    COMMIT=$(grep -c '^COMMIT_HEAD' probe.log)
    ENQ=$(grep -c '^ENQUEUE_PARTIAL' probe.log)
    pkill -KILL dipp 2>/dev/null
    sleep 1

    # Restart and read the depths the fresh daemon sees.
    (setsid ./dipp -i CAN -p can0 -a 5423 >> probe.log 2>&1 &)
    sleep 6
    DEPTHS=$(grep '^STARTUP_QUEUES' probe.log | tail -n 1)
    DONE=$(grep -c 'Pipeline fully executed' probe.log)
    echo "trial $t: killed at progress=3 | commits=$COMMIT enqueues=$ENQ | after restart: $DEPTHS | completions=$DONE"
    t=$((t + 1))
done
pkill -KILL dipp 2>/dev/null
echo "=== done ==="
