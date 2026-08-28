#!/bin/sh
# Execute ONE run and leave a self-describing directory. Board side.
#
#   run.sh --label adaptive-mmap-01 --pipeline 1 --storage MMAP \
#          --heuristic BEST_EFFORT --load populate --n 20 --images 1 [--seed 43]
#          [--keep-cost-cache] [--no-trace]
#
# Writes runs/<label>/{board.json,console.log,trace.json,storage.vmem,pre.txt,post.txt}
# The host driver merges board.json with what only it can know (Otii energy,
# transfer hashes) into the final RUN.json.
set -u

DIR=/home/root/tmp-experiments
IMG=/mnt/flash/2025-11-10/sentinel2-dataset/amager-no-clouds.bayerRG

LABEL=""; PIPELINE=2; STORAGE=MMAP; HEURISTIC=BEST_EFFORT
LOAD=populate; N=20; IMAGES=1; SEED=43; KEEP_CACHE=0; TRACE=1
while [ $# -gt 0 ]; do
    case "$1" in
        --label) LABEL="$2"; shift 2;;
        --pipeline) PIPELINE="$2"; shift 2;;
        --storage) STORAGE="$2"; shift 2;;
        --heuristic) HEURISTIC="$2"; shift 2;;
        --load) LOAD="$2"; shift 2;;
        --n) N="$2"; shift 2;;
        --images) IMAGES="$2"; shift 2;;
        --seed) SEED="$2"; shift 2;;
        --keep-cost-cache) KEEP_CACHE=1; shift;;
        --no-trace) TRACE=0; shift;;
        *) echo "unknown option: $1" >&2; exit 2;;
    esac
done
[ -n "$LABEL" ] || { echo "--label is required" >&2; exit 2; }

# grep -c prints "0" and exits 1 on no-match, and prints nothing when the file
# is missing. Both break the JSON in different ways, so count through here.
cnt() {
    c=$(grep -c "$1" "$2" 2>/dev/null)
    echo "${c:-0}"
}

cd "$DIR" || exit 1
OUT="$DIR/runs/$LABEL"
rm -rf "$OUT"; mkdir -p "$OUT"

# /mnt/flash is never mounted at boot and the dataset lives there.
mountpoint -q /mnt/flash || mount -o ro /dev/sda1 /mnt/flash 2>/dev/null
[ -f "$IMG" ] || { echo "dataset not readable: $IMG" >&2; exit 1; }

# The A8 leak costs ~2.6 GB per run against ~4.7 GB free, so a campaign dies
# after two runs if nobody checks. Fail loudly here rather than half-way through.
AVAIL_KB=$(df -k /home/root | awk 'NR==2 {print $4}')
if [ "$AVAIL_KB" -lt 1048576 ]; then
    echo "ABORT: only ${AVAIL_KB}kB free on /home/root; need >=1GB" >&2
    exit 1
fi

pkill -KILL dipp 2>/dev/null
sleep 1

# Clear state. Whether cost.cache survives is an experimental-design choice, not
# a detail: clearing it makes every run measure first-execution cost, keeping it
# lets a run inherit calibration from its predecessor. C4 wants a warm cache with
# a discarded warm-up batch; the energy characterisation wants known-cold.
rm -f /usr/share/dipp/queue_file /usr/share/dipp/partially_processed_queue_file
[ "$KEEP_CACHE" -eq 1 ] || rm -f /usr/share/dipp/cost.cache
find /usr/share/dipp/data -type f -exec rm -f {} + 2>/dev/null

{
    echo "=== pre ==="; date -u
    free; echo; df -h /home/root
    echo "shm_segments=$(( $(wc -l < /proc/sysvipc/shm) - 1 ))"
    echo "cost_cache_kept=$KEEP_CACHE"
} > "$OUT/pre.txt" 2>&1

TRACE_ARG=""
[ "$TRACE" -eq 1 ] && { mkdir -p traces; rm -f traces/$LABEL.json; TRACE_ARG="-t traces/$LABEL.json"; }

STORAGE_MODE="$STORAGE" HEURISTIC="$HEURISTIC" \
    setsid ./dipp -i CAN -p can0 -a 5423 $TRACE_ARG > "$OUT/console.log" 2>&1 &
sleep 5

# A run against a daemon that never started produces a clean-looking null
# result, so confirm rather than assume.
if ! pidof dipp > /dev/null 2>&1; then
    sleep 3
    if ! pidof dipp > /dev/null 2>&1; then
        echo "ABORT: daemon did not start" >&2
        echo '{"error":"daemon_did_not_start"}' > "$OUT/board.json"
        exit 1
    fi
fi

START=$(date -u +%s)
echo "--- feeding $N batches (load=$LOAD pipeline=$PIPELINE images=$IMAGES) ---"
if [ "$LOAD" = "poisson" ]; then
    # Seeded, so the offered stream is identical across runs -- srand(43) is what
    # the recovered board script used.
    awk -v n="$N" -v mu=3 -v seed="$SEED" 'BEGIN{
        srand(seed); print 0
        for (i = 1; i < n; i++) { u = rand(); if (u == 0) u = 0.000001; print -mu * log(u) }
    }' | while read -r d; do
        sleep "$d"
        ./emulate_camera "$IMAGES" "$PIPELINE" 123 "$IMG" >/dev/null 2>&1
    done
else
    i=1
    while [ "$i" -le "$N" ]; do
        ./emulate_camera "$IMAGES" "$PIPELINE" 123 "$IMG" >/dev/null 2>&1
        i=$((i + 1))
    done
fi

echo "--- draining ---"
LAST=-1; STABLE=0
while [ "$STABLE" -lt 4 ]; do
    sleep 5
    NOW=$(cnt 'fully executed' "$OUT/console.log")
    if [ "$NOW" = "$LAST" ]; then STABLE=$((STABLE + 1)); else STABLE=0; fi
    LAST=$NOW
done
END=$(date -u +%s)

# SIGINT is handled only when tracing is on; SIGTERM always is. Use TERM.
pkill -TERM dipp 2>/dev/null
sleep 3
pkill -KILL dipp 2>/dev/null

{
    echo "=== post ==="; date -u
    free; echo; df -h /home/root
    echo "shm_segments=$(( $(wc -l < /proc/sysvipc/shm) - 1 ))"
    echo "orphan_files=$(ls /usr/share/dipp/data 2>/dev/null | grep -c .)"
} > "$OUT/post.txt" 2>&1

[ "$TRACE" -eq 1 ] && cp "traces/$LABEL.json" "$OUT/trace.json" 2>/dev/null
# The daemon binds whichever storage.vmem is in its cwd (VMEM_DEFINE_FILE takes
# a relative path), so record the one that was actually in force.
cp "$DIR/storage.vmem" "$OUT/storage.vmem" 2>/dev/null

EXECS=$(cnt '^EXEC ' "$OUT/console.log")
DONE=$(cnt 'fully executed' "$OUT/console.log")
BUILD=$(grep -m1 '^SIPP build: ' "$OUT/console.log" 2>/dev/null | sed 's/^SIPP build: //')
PAGESZ=$(awk '/^KernelPageSize/{print $2*1024; exit}' /proc/self/smaps 2>/dev/null)

cat > "$OUT/board.json" <<JSON
{
  "label": "$LABEL",
  "pipeline": $PIPELINE,
  "storage": "$STORAGE",
  "heuristic": "$HEURISTIC",
  "load": "$LOAD",
  "n": $N,
  "images": $IMAGES,
  "seed": $SEED,
  "cost_cache_kept": $KEEP_CACHE,
  "traced": $TRACE,
  "started_utc": $START,
  "ended_utc": $END,
  "duration_s": $((END - START)),
  "build_id": "$BUILD",
  "page_size": ${PAGESZ:-0},
  "kernel": "$(uname -r)",
  "n_executions": $EXECS,
  "n_completed": $DONE,
  "queue_sync_fail": $(cnt QUEUE_SYNC_FAIL "$OUT/console.log"),
  "queue_corrupt": $(cnt QUEUE_CORRUPT "$OUT/console.log"),
  "queue_rollback": $(cnt QUEUE_ROLLBACK "$OUT/console.log"),
  "queue_mismatch": $(cnt QUEUE_MISMATCH "$OUT/console.log"),
  "ingest_reject": $(cnt INGEST_REJECT "$OUT/console.log")
}
JSON

echo "=== done: $LABEL  executions=$EXECS completed=$DONE duration=$((END - START))s ==="
