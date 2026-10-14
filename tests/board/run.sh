#!/bin/sh
# Execute ONE run and leave a self-describing directory. Board side.
#
#   run.sh --label adaptive-mmap-01 --pipeline 1 --storage MMAP \
#          --heuristic BEST_EFFORT --load populate --n 20 --images 1 [--seed 43] \
#          --platform disco2-bol.env --soc0 0.9 --phase0 0.25 --slo 15
#   run.sh ... --load poisson --n 100 --mean 3 --slo 15 ...
#   run.sh ... --load trace --trace traces/<md5>.trace --idle-window 10 \
#          --ff-chunk 60 --ff-tick 0.5 --tail-ff 5726 ...
# --load trace replays an arrival trace with per-frame SLOs, fast-forwarding
# the battery across the gaps between bursts (feed_trace.sh documents it).
#          [--image cs12_validation_00.bayerRG | --image-prefix cs12_test_matched_]
# --image-prefix (trace load only) rotates the frames through every
# $IMG_DIR/<prefix>*.bayerRG, sorted, one per frame (feed_trace.sh --image-list).
#          [--keep-cost-cache] [--no-trace]
#
# Writes runs/<label>/{board.json,console.log,trace.json,storage.vmem,pre.txt,post.txt}
# The host driver merges board.json with what only it can know (Otii energy,
# transfer hashes) into the final RUN.json.
set -u

DIR=/home/root/tmp-experiments
IMG_DIR=/mnt/flash/cs12-frames
IMAGE=cs12_validation_00.bayerRG

LABEL=""; PIPELINE=2; STORAGE=MMAP; HEURISTIC=BEST_EFFORT
LOAD=populate; N=20; IMAGES=1; SEED=43; KEEP_CACHE=0; TRACE=1; MIN_DRAIN=""; ENVELOPE_IVL=""
SCHED_Q_MED=""; SCHED_Q_LOW=""; SCHED_GRAN=""; SCHED_GATE_MED_MS=""; SCHED_GATE_LOW_MS=""
SCHED_WINDOW=""; SCHED_FRAME_OVERHEAD_MS=""; SCHED_GUARD=""; SCHED_ACCEPT=""; SCHED_K_X10=""; SCHED_SMAX_HIGH_MS=""; SCHED_SMAX_MED_MS=""
PLATFORM=""; SOC0=""; PHASE0=""
IMAGE_PREFIX=""; SLO=""; MEAN=""; TRACEF=""; IDLE_WIN=""; FF_CHUNK=""; FF_TICK=""; TAIL_FF=""; COST_SEED=""
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
        --image) IMAGE="$2"; shift 2;;
        --image-prefix) IMAGE_PREFIX="$2"; shift 2;;
        --keep-cost-cache) KEEP_CACHE=1; shift;;
        --no-trace) TRACE=0; shift;;
        --min-drain) MIN_DRAIN="$2"; shift 2;;
        --envelope-ivl) ENVELOPE_IVL="$2"; shift 2;;
        --sched-q-med) SCHED_Q_MED="$2"; shift 2;;
        --sched-q-low) SCHED_Q_LOW="$2"; shift 2;;
        --sched-granularity) SCHED_GRAN="$2"; shift 2;;
        --sched-gate-med-ms) SCHED_GATE_MED_MS="$2"; shift 2;;
        --sched-gate-low-ms) SCHED_GATE_LOW_MS="$2"; shift 2;;
        --sched-window) SCHED_WINDOW="$2"; shift 2;;
        --sched-guard) SCHED_GUARD="$2"; shift 2;;
        --sched-accept) SCHED_ACCEPT="$2"; shift 2;;
        --sched-k-x10) SCHED_K_X10="$2"; shift 2;;
        --sched-smax-high-ms) SCHED_SMAX_HIGH_MS="$2"; shift 2;;
        --sched-smax-med-ms) SCHED_SMAX_MED_MS="$2"; shift 2;;
        --sched-frame-overhead-ms) SCHED_FRAME_OVERHEAD_MS="$2"; shift 2;;
        --platform) PLATFORM="$2"; shift 2;;
        --soc0) SOC0="$2"; shift 2;;
        --phase0) PHASE0="$2"; shift 2;;
        --slo) SLO="$2"; shift 2;;
        --mean) MEAN="$2"; shift 2;;
        --trace) TRACEF="$2"; shift 2;;
        --idle-window) IDLE_WIN="$2"; shift 2;;
        --ff-chunk) FF_CHUNK="$2"; shift 2;;
        --ff-tick) FF_TICK="$2"; shift 2;;
        --tail-ff) TAIL_FF="$2"; shift 2;;
        --cost-seed) COST_SEED="$2"; shift 2;;
        *) echo "unknown option: $1" >&2; exit 2;;
    esac
done
[ -n "$LABEL" ] || { echo "--label is required" >&2; exit 2; }
# The stall limit of the drain (below) has no default: a run whose first
# batch outlasts it is cut off with nothing completed (2026-10-03).
[ -n "$MIN_DRAIN" ] || { echo "--min-drain <s> is required (longer than the slowest batch)" >&2; exit 2; }
# The scheduling policy's parameters (src/include/sched_policy.h): stated for
# every run, so a result never depends on a compiled default.
for pair in sched-q-med:SCHED_Q_MED sched-q-low:SCHED_Q_LOW sched-granularity:SCHED_GRAN \
            sched-gate-med-ms:SCHED_GATE_MED_MS sched-gate-low-ms:SCHED_GATE_LOW_MS \
            sched-window:SCHED_WINDOW sched-frame-overhead-ms:SCHED_FRAME_OVERHEAD_MS sched-guard:SCHED_GUARD \
            sched-accept:SCHED_ACCEPT sched-k-x10:SCHED_K_X10 sched-smax-high-ms:SCHED_SMAX_HIGH_MS \
            sched-smax-med-ms:SCHED_SMAX_MED_MS; do
    eval "val=\${${pair#*:}}"
    [ -n "$val" ] || { echo "--${pair%%:*} is required" >&2; exit 2; }
done
# The battery simulator has no defaults (battery_simulator.h): the platform
# and the initial conditions are stated for every run or the run does not
# happen.
[ -n "$PLATFORM" ] || { echo "--platform is required (experiments/platforms/*.env)" >&2; exit 2; }
[ -n "$SOC0" ] || { echo "--soc0 is required" >&2; exit 2; }
[ -n "$PHASE0" ] || { echo "--phase0 is required" >&2; exit 2; }
# The cost store starts from a seed file (tools/seed_cost_store.py, staged as
# cost.seed) or, stated explicitly, from nothing.
[ -n "$COST_SEED" ] || { echo "--cost-seed <file>|none is required" >&2; exit 2; }
if [ "$COST_SEED" != none ] && [ "$KEEP_CACHE" -eq 1 ]; then
    echo "--cost-seed and --keep-cost-cache contradict each other" >&2; exit 2
fi
# The load, and what each kind needs -- no defaults. A trace carries its own
# per-frame SLOs; the synthetic loads take one SLO for every frame (the paper
# used 15 s), and Poisson its mean inter-arrival time.
case "$LOAD" in
    populate) [ -n "$SLO" ] || { echo "--load populate needs --slo" >&2; exit 2; } ;;
    poisson)  [ -n "$SLO" ] && [ -n "$MEAN" ] || { echo "--load poisson needs --slo and --mean" >&2; exit 2; } ;;
    trace)
        for pair in trace:TRACEF idle-window:IDLE_WIN ff-chunk:FF_CHUNK ff-tick:FF_TICK tail-ff:TAIL_FF; do
            eval "val=\${${pair#*:}}"
            [ -n "$val" ] || { echo "--load trace needs --${pair%%:*}" >&2; exit 2; }
        done ;;
    *) echo "unknown --load: $LOAD (populate | poisson | trace)" >&2; exit 2 ;;
esac
IMG="$IMG_DIR/$IMAGE"
if [ -n "$IMAGE_PREFIX" ] && [ "$LOAD" != trace ]; then
    echo "--image-prefix is for --load trace only" >&2; exit 2
fi

# grep -c prints "0" and exits 1 on no-match, and prints nothing when the file
# is missing. Both break the JSON in different ways, so count through here.
cnt() {
    c=$(grep -c "$1" "$2" 2>/dev/null)
    echo "${c:-0}"
}

cd "$DIR" || exit 1
[ -f "$PLATFORM" ] || { echo "platform file not found: $PLATFORM" >&2; exit 2; }
if [ "$LOAD" = "trace" ]; then
    ./feed_trace.sh --check --trace "$TRACEF" || exit 2
    TRACE_MD5=$(md5sum "$TRACEF" | cut -d' ' -f1)
fi
# Only the preset and the flags define the power model: anything DIPP_BATT_*
# left in this shell would be a hidden input, so clear it first.
for v in $(env | sed -n 's/^\(DIPP_BATT_[A-Z0-9_]*\)=.*/\1/p'); do unset "$v"; done
case "$PLATFORM" in /*) PLATFORM_PATH="$PLATFORM";; *) PLATFORM_PATH="./$PLATFORM";; esac
set -a; . "$PLATFORM_PATH"; set +a
export DIPP_BATT_SOC0="$SOC0" DIPP_BATT_PHASE0="$PHASE0"
PLATFORM_MD5=$(md5sum "$PLATFORM" | cut -d' ' -f1)
PROFILE_MD5=$(md5sum "$DIPP_BATT_PROFILE" 2>/dev/null | cut -d' ' -f1)
OUT="$DIR/runs/$LABEL"
rm -rf "$OUT"; mkdir -p "$OUT"

# /mnt/flash is never mounted at boot and the dataset lives there.
mountpoint -q /mnt/flash || mount -o ro /dev/sda1 /mnt/flash 2>/dev/null
if [ -n "$IMAGE_PREFIX" ]; then
    ls $IMG_DIR/$IMAGE_PREFIX*.bayerRG 2>/dev/null | sort > "$OUT/images.txt"
    [ -s "$OUT/images.txt" ] || { echo "no images match $IMG_DIR/$IMAGE_PREFIX*.bayerRG" >&2; exit 1; }
    IMAGE="prefix:$IMAGE_PREFIX($(grep -c . "$OUT/images.txt"))"
else
    [ -f "$IMG" ] || { echo "dataset not readable: $IMG" >&2; exit 1; }
fi

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
# The empty SD slot (uSDHC1, 30b40000.mmc) has a bouncing card-detect line:
# ~40 GPIO interrupts per second that keep the MMC core rescanning (load 0.5
# on an idle board, 2026-09-21). The eMMC is on uSDHC3 and /mnt/flash is USB,
# so the host can go; unbinding it before a run removes that noise.
echo 30b40000.mmc > /sys/bus/platform/drivers/sdhci-esdhc-imx/unbind 2>/dev/null
rm -f /usr/share/dipp/queue_file /usr/share/dipp/partially_processed_queue_file
[ "$KEEP_CACHE" -eq 1 ] || rm -f /usr/share/dipp/cost.cache
if [ "$COST_SEED" != none ]; then
    [ -f "$COST_SEED" ] || { echo "cost seed not found: $COST_SEED" >&2; exit 2; }
    cp "$COST_SEED" /usr/share/dipp/cost.cache || { echo "cannot install $COST_SEED" >&2; exit 1; }
    COST_SEED_MD5=$(md5sum "$COST_SEED" | cut -d' ' -f1)
fi
rm -f /home/root/logs/* 2>/dev/null
find /usr/share/dipp/data -type f -exec rm -f {} + 2>/dev/null

{
    echo "=== pre ==="; date -u
    free; echo; df -h /home/root
    echo "shm_segments=$(( $(wc -l < /proc/sysvipc/shm) - 1 ))"
    echo "cost_cache_kept=$KEEP_CACHE"
} > "$OUT/pre.txt" 2>&1

TRACE_ARG=""
[ "$TRACE" -eq 1 ] && { mkdir -p traces; rm -f traces/$LABEL.json; TRACE_ARG="-t traces/$LABEL.json"; }

STORAGE_MODE="$STORAGE" \
    DIPP_SCHED_Q_MED="$SCHED_Q_MED" DIPP_SCHED_Q_LOW="$SCHED_Q_LOW" DIPP_SCHED_GRANULARITY="$SCHED_GRAN" \
    DIPP_SCHED_GATE_MED_MS="$SCHED_GATE_MED_MS" DIPP_SCHED_GATE_LOW_MS="$SCHED_GATE_LOW_MS" \
    DIPP_SCHED_WINDOW="$SCHED_WINDOW" DIPP_SCHED_FRAME_OVERHEAD_MS="$SCHED_FRAME_OVERHEAD_MS" \
    DIPP_SCHED_GUARD="$SCHED_GUARD" DIPP_SCHED_ACCEPT="$SCHED_ACCEPT" DIPP_SCHED_K_X10="$SCHED_K_X10" \
    DIPP_SCHED_SMAX_HIGH_MS="$SCHED_SMAX_HIGH_MS" DIPP_SCHED_SMAX_MED_MS="$SCHED_SMAX_MED_MS" \
    setsid ./dipp -i CAN -p can0 -a 5423 $TRACE_ARG > "$OUT/console.log" 2>&1 &
sleep 5
# Optional operating-envelope sampler (R1.5): memory of SIPP's processes,
# shared memory, persisted data, available memory, every ENVELOPE_IVL s.
if [ -n "${ENVELOPE_IVL:-}" ]; then
    sh ./envelope_sampler.sh "$ENVELOPE_IVL" "$OUT/envelope.txt" &
fi

# A run against a daemon that never started produces a clean-looking null
# result, so confirm rather than assume.
if ! pidof dipp > /dev/null 2>&1; then
    sleep 3
    if ! pidof dipp > /dev/null 2>&1; then
        echo "ABORT: daemon did not start" >&2
        grep 'BATTERY_CONFIG_ERROR\|FATAL' "$OUT/console.log" >&2
        if grep -q BATTERY_CONFIG_ERROR "$OUT/console.log"; then
            echo '{"error":"battery_config_error"}' > "$OUT/board.json"
        else
            echo '{"error":"daemon_did_not_start"}' > "$OUT/board.json"
        fi
        exit 1
    fi
fi

# The daemon creates its message queue (key 71) at startup; feed nothing
# until it exists -- the trace feeder's first `dipp_ctl mark` needs it.
i=0
until awk 'NR > 1 && $1 == 71 { f = 1 } END { exit !f }' /proc/sysvipc/msg; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
        echo "ABORT: daemon queue (key 71) absent 60 s after start" >&2
        echo '{"error":"daemon_queue_absent"}' > "$OUT/board.json"
        pkill -KILL dipp 2>/dev/null
        exit 1
    fi
    sleep 1
done

START=$(date -u +%s)
echo "--- feeding $N batches (load=$LOAD pipeline=$PIPELINE images=$IMAGES) ---"
if [ "$LOAD" = "poisson" ]; then
    # Seeded, so the offered stream is identical across runs -- srand(43) is what
    # the recovered board script used.
    awk -v n="$N" -v mu="$MEAN" -v seed="$SEED" 'BEGIN{
        srand(seed); print 0
        for (i = 1; i < n; i++) { u = rand(); if (u == 0) u = 0.000001; print -mu * log(u) }
    }' | while read -r d; do
        sleep "$d"
        ./emulate_camera "$IMAGES" "$PIPELINE" 123 "$IMG" "$SLO" >/dev/null 2>&1
    done
elif [ "$LOAD" = "trace" ]; then
    if [ -n "$IMAGE_PREFIX" ]; then IMGARG="--image-list $OUT/images.txt"; else IMGARG="--image $IMG"; fi
    ./feed_trace.sh --trace "$TRACEF" --pipeline "$PIPELINE" --images "$IMAGES" \
        $IMGARG --console "$OUT/console.log" --feed-log "$OUT/feed.log" \
        --dipp-ctl ./dipp_ctl --camera ./emulate_camera --idle-window "$IDLE_WIN" \
        --ff-chunk "$FF_CHUNK" --ff-tick "$FF_TICK" --tail-ff "$TAIL_FF" \
        || echo "FEED_FAILED" >> "$OUT/feed.log"
else
    i=1
    while [ "$i" -le "$N" ]; do
        ./emulate_camera "$IMAGES" "$PIPELINE" 123 "$IMG" "$SLO" >/dev/null 2>&1
        i=$((i + 1))
    done
fi

echo "--- draining ---"
# Done when every batch fed has completed: N for populate/poisson; for a
# trace, the frames fed less those dropped at ingest. MIN_DRAIN is only the
# stall limit -- no completion for that long ends the run, recorded as
# drain_stalled=1 (a halted backlog after the trace's tail, or a hang).
# The old rule ended the run MIN_DRAIN seconds after the count last changed,
# counting from the end of feeding, so a first batch longer than MIN_DRAIN
# (5 frames at high effort, ~50 s against 30 s) was cut off with nothing
# completed -- and still recorded as a run (size sweep, 2026-10-03).
if [ "$LOAD" = "trace" ]; then
    FED=$(sed -n 's/^TRACE_END frames=\([0-9]*\).*/\1/p' "$OUT/feed.log" 2>/dev/null)
    EXPECT=$(( ${FED:-0} - $(cnt '^INGEST_DROP' "$OUT/console.log") ))
else
    EXPECT=$(( N - $(cnt '^INGEST_DROP' "$OUT/console.log") ))
fi
STALLED=0
LAST=-1; LAST_CHANGE=$(date -u +%s)
while :; do
    sleep 5
    NOW=$(cnt 'fully executed' "$OUT/console.log")
    T=$(date -u +%s)
    if [ "$NOW" -ge "$EXPECT" ]; then break; fi
    if [ "$NOW" != "$LAST" ]; then LAST_CHANGE=$T; fi
    LAST=$NOW
    if [ $(( T - LAST_CHANGE )) -ge "$MIN_DRAIN" ]; then STALLED=1; break; fi
done
END=$(date -u +%s)

[ -n "${ENVELOPE_IVL:-}" ] && touch "$OUT/envelope.txt.stop"
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

# The modules log their own decisions to /home/root/logs/, one file per fork.
# These are the only record of what the models actually predicted, so they are
# a first-class artifact of a run, not a debug aid.
cat /home/root/logs/tflite_classification_* 2>/dev/null | grep 'Top class' > "$OUT/predictions.txt"
cat /home/root/logs/tflite_segmentation_* 2>/dev/null | grep 'Kept pixels' > "$OUT/kept_pixels.txt"
HIST=$(awk '{for(i=1;i<=NF;i++) if($i=="class:"){c[$(i+1)]++}} END{s="";for(k in c) s=s k ":" c[k] " ";print s}' "$OUT/predictions.txt" 2>/dev/null)
NPRED=$(cnt . "$OUT/predictions.txt")

EXECS=$(cnt '^EXEC ' "$OUT/console.log")
DONE=$(cnt 'fully executed' "$OUT/console.log")
# The banner ends with CR, which grep keeps; an unescaped control character
# inside a JSON string makes the whole record unparseable host-side.
BUILD=$(grep -m1 '^SIPP build: ' "$OUT/console.log" 2>/dev/null \
        | sed 's/^SIPP build: //' | tr -d '\000-\037')
PAGESZ=$(awk '/^KernelPageSize/{print $2*1024; exit}' /proc/self/smaps 2>/dev/null)

cat > "$OUT/board.json" <<JSON
{
  "label": "$LABEL",
  "pipeline": $PIPELINE,
  "storage": "$STORAGE",
  "heuristic_arg_ignored": "$HEURISTIC",
  "load": "$LOAD",
  "n": $N,
  "images": $IMAGES,
  "seed": $SEED,
  "image": "$IMAGE",
  "cost_cache_kept": $KEEP_CACHE,
  "cost_seed": "$COST_SEED",
  "cost_seed_md5": "${COST_SEED_MD5:-}",
  "platform": "$PLATFORM",
  "platform_md5": "$PLATFORM_MD5",
  "profile": "$DIPP_BATT_PROFILE",
  "profile_md5": "${PROFILE_MD5:-missing}",
  "soc0": $SOC0,
  "phase0": $PHASE0,
  "slo_s": ${SLO:-null},
  "mean_s": ${MEAN:-null},
  "trace": "${TRACEF}",
  "trace_md5": "${TRACE_MD5:-}",
  "ff_chunk_s": ${FF_CHUNK:-null},
  "ff_tick_s": ${FF_TICK:-null},
  "idle_window_s": ${IDLE_WIN:-null},
  "tail_ff_s": ${TAIL_FF:-null},
  "trace_end": "$(grep '^TRACE_END\|^FEED_FAILED' "$OUT/feed.log" 2>/dev/null | tail -n 1 | tr -d '\000-\037\"')",
  "traced": $TRACE,
  "started_utc": $START,
  "ended_utc": $END,
  "duration_s": $((END - START)),
  "build_id": "$BUILD",
  "page_size": ${PAGESZ:-0},
  "kernel": "$(uname -r)",
  "n_executions": $EXECS,
  "n_completed": $DONE,
  "n_expected": $EXPECT,
  "min_drain_s": $MIN_DRAIN,
  "sched_q_med": "$SCHED_Q_MED",
  "sched_q_low": "$SCHED_Q_LOW",
  "sched_granularity": "$SCHED_GRAN",
  "sched_gate_med_ms": $SCHED_GATE_MED_MS,
  "sched_gate_low_ms": $SCHED_GATE_LOW_MS,
  "sched_window": $SCHED_WINDOW,
  "sched_guard": "$SCHED_GUARD",
  "sched_accept": "$SCHED_ACCEPT",
  "sched_k_x10": $SCHED_K_X10,
  "sched_smax_high_ms": $SCHED_SMAX_HIGH_MS,
  "sched_smax_med_ms": $SCHED_SMAX_MED_MS,
  "sched_frame_overhead_ms": $SCHED_FRAME_OVERHEAD_MS,
  "sched_config": "$(grep -m1 '^SCHED_CONFIG ' "$OUT/console.log" 2>/dev/null | tr -d '\000-\037\"')",
  "preemptions": $(cnt '^PREEMPT ' "$OUT/console.log"),
  "cost_flags": $(cnt '^COST_FLAG ' "$OUT/console.log"),
  "drain_stalled": $STALLED,
  "queue_sync_fail": $(cnt QUEUE_SYNC_FAIL "$OUT/console.log"),
  "queue_corrupt": $(cnt QUEUE_CORRUPT "$OUT/console.log"),
  "queue_rollback": $(cnt QUEUE_ROLLBACK "$OUT/console.log"),
  "queue_mismatch": $(cnt QUEUE_MISMATCH "$OUT/console.log"),
  "ingest_reject": $(cnt INGEST_REJECT "$OUT/console.log"),
  "n_predictions": $NPRED,
  "class_histogram": "$HIST"
}
JSON

echo "=== done: $LABEL  executions=$EXECS completed=$DONE duration=$((END - START))s ==="
