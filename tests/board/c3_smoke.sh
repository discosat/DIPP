#!/bin/sh
# C3 smoke test: kill-and-restart the daemon repeatedly while it processes a
# fixed set of batches, then check that every batch completed exactly once.
#
#   c3_smoke.sh [trials] [batches] [pipeline]
#
# SIGKILL is deliberate: it is the closest approximation to a power cut that can
# be produced without cutting power, and it gives the daemon no chance to flush
# anything. Batches are identified by UUID at both ends, so loss and duplication
# are both directly countable.
set -u
TRIALS=${1:-20}
NBATCH=${2:-20}
PIPELINE=${3:-2}
IMG=/mnt/flash/2025-11-10/sentinel2-dataset/amager-no-clouds.bayerRG
DIR=/home/root/tmp-experiments
cd "$DIR" || exit 1

echo "=== C3: $TRIALS kill cycles, $NBATCH batches, pipeline $PIPELINE ==="

# Clean slate
pkill -KILL dipp 2>/dev/null
sleep 1
rm -f /usr/share/dipp/queue_file /usr/share/dipp/partially_processed_queue_file /usr/share/dipp/cost.cache
find /usr/share/dipp/data -type f -exec rm -f {} + 2>/dev/null
rm -f c3.log c3_fed.txt

# Exactly one daemon at a time. pidof returns a SPACE-SEPARATED list, so any
# quoting of "$(pidof dipp)" passes it to kill as a single bogus argument and
# the kill silently fails -- which lets daemons accumulate and quietly
# invalidates the whole test.
kill_daemon() {
    pkill -KILL dipp 2>/dev/null
    sleep 1
    if pidof dipp > /dev/null 2>&1; then
        echo "WARNING: dipp still running after SIGKILL"
    fi
}

start_daemon() {
    if pidof dipp > /dev/null 2>&1; then
        echo "WARNING: daemon already running, not starting another"
        return
    fi
    (setsid ./dipp -i CAN -p can0 -a 5423 >> c3.log 2>&1 &)
    sleep 2
}

start_daemon
echo "--- feeding $NBATCH batches ---"
i=1
while [ "$i" -le "$NBATCH" ]; do
    ./emulate_camera 1 "$PIPELINE" 123 "$IMG" 2>/dev/null | grep '^Image sent!' >> c3_fed.txt
    i=$((i + 1))
done

echo "--- $TRIALS kill/restart cycles ---"
t=1
while [ "$t" -le "$TRIALS" ]; do
    sleep 2
    if pidof dipp > /dev/null 2>&1; then
        echo "cycle $t: killing $(pidof dipp)"
        kill_daemon
    else
        echo "cycle $t: daemon was not running"
    fi
    start_daemon
    t=$((t + 1))
done

echo "--- draining ---"
LAST=-1
STABLE=0
while [ "$STABLE" -lt 6 ]; do
    sleep 5
    NOW=$(grep -c 'Pipeline fully executed successfully' c3.log)
    if [ "$NOW" = "$LAST" ]; then STABLE=$((STABLE + 1)); else STABLE=0; fi
    LAST=$NOW
done

pkill -TERM dipp 2>/dev/null
sleep 3

echo "=== RESULT ==="
FED=$(grep -c 'uuid=' c3_fed.txt)
FED_U=$(sed 's/.*uuid=\([^ ]*\).*/\1/' c3_fed.txt | sort -u | grep -c .)
DONE_ALL=$(grep -c 'Pipeline fully executed successfully' c3.log)
DONE_U=$(grep 'Pipeline fully executed successfully' c3.log | sed 's/.*uuid=\([^ ]*\).*/\1/' | sort -u | grep -c .)
echo "fed_batches=$FED fed_unique=$FED_U"
echo "completions=$DONE_ALL completed_unique=$DONE_U"
echo "lost=$((FED_U - DONE_U))"
echo "duplicate_completions=$((DONE_ALL - DONE_U))"
echo "missing_file_errors=$(grep -c 'refusing to fabricate' c3.log)"
echo "short_file_errors=$(grep -c 'refusing to pad' c3.log)"
echo "orphan_files=$(ls /usr/share/dipp/data/ 2>/dev/null | grep -c .)"
echo "queue_selects=$(grep -c '^QUEUE_SELECT' c3.log)"
echo "queue_removes=$(grep -c '^QUEUE_REMOVE' c3.log)"
echo "queue_mismatches=$(grep -c '^QUEUE_MISMATCH' c3.log)"
echo "ingest_rejects=$(grep -c '^INGEST_REJECT' c3.log)"
echo "removes_by_reason:"
grep -oE 'reason=[a-z_]+' c3.log | sort | uniq -c
echo "never_seen_by_daemon:"
for u in $(sed 's/.*uuid=\([^ ]*\).*/\1/' c3_fed.txt | sort -u); do
    grep -q "$u" c3.log || echo "  NEVER_SEEN $u"
done
echo "seen_but_incomplete:"
grep 'Pipeline fully executed' c3.log | sed 's/.*uuid=\([^ ]*\).*/\1/' | sort -u > /tmp/c3_done
for u in $(sed 's/.*uuid=\([^ ]*\).*/\1/' c3_fed.txt | sort -u); do
    if grep -q "$u" c3.log && ! grep -q "$u" /tmp/c3_done; then echo "  INCOMPLETE $u"; fi
done
echo "concurrent_daemon_warnings=$(grep -c 'WARNING: daemon already running' c3_result.txt 2>/dev/null || echo 0)"
