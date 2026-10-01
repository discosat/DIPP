#!/bin/sh
# Replay an arrival trace against a running dipp. Board side; run.sh calls it.
#
#   feed_trace.sh --check --trace FILE
#   feed_trace.sh --trace FILE --pipeline N --images N --image PATH
#                 --console LOG --feed-log FILE --dipp-ctl PATH --camera PATH
#                 --idle-window S --ff-chunk S --ff-tick S --tail-ff S
#
# Trace format (tools/make_arrival_trace.py writes it):
#   '#' lines are comments/header and are ignored here.
#   One frame per line, six whitespace-separated columns:
#     t_s       capture time on the COMPRESSED clock: seconds since the trace
#               start, excluding every fast-forwarded gap; non-decreasing
#     ff_s      simulated seconds to fast-forward BEFORE this frame (the
#               compressed part of the gap since the previous frame), >= 0
#     scene     integer id, logged by `dipp_ctl mark`
#     datatake  string id (no spaces), carried into feed.log for analysis
#     slo_s     the frame's SLO, a positive integer (emulate_camera's 5th arg)
#     class     urgent | routine
#
# Timing. A frame's true time on the orbit is t_s + (all ff_s so far). The
# battery simulator runs 1:1 with the wall clock, so "simulated now" =
# wall time since the trace started + everything fast-forwarded. A gap with
# ff_s > 0 is fast-forwarded only once the daemon is quiescent -- its last
# SCHED_STATE is idle or halted, continuously, for --idle-window seconds --
# because the time spent finishing the backlog is part of the gap: the
# satellite processes in real time, and only the idle remainder is skipped.
# It then advances in --ff-chunk steps, pausing --ff-tick between them so
# energy-halted batches get to run as the battery recharges, and never past
# the next frame's time. Every advance is confirmed from the daemon's own
# BATTERY_FF lines before the frame is fed.
#
# After the last frame, --tail-ff S lets the backlog finish: while the daemon
# is halted it fast-forwards in chunks, up to S simulated seconds in total,
# until it is idle. 0 disables it. Whatever is still queued then is reported
# as unfinished, not waited for.
#
# Nothing here has a default (see the no-fallbacks rule in the plan): a
# missing argument or a malformed trace line stops the run before the daemon
# is fed anything.
set -u

TRACE=""; PIPELINE=""; IMAGES=""; IMG=""; CONSOLE=""; FEEDLOG=""
CTL=""; CAM=""; IDLE=""; CHUNK=""; TICK=""; TAIL=""; CHECK=0
while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECK=1; shift;;
        --trace) TRACE="$2"; shift 2;;
        --pipeline) PIPELINE="$2"; shift 2;;
        --images) IMAGES="$2"; shift 2;;
        --image) IMG="$2"; shift 2;;
        --console) CONSOLE="$2"; shift 2;;
        --feed-log) FEEDLOG="$2"; shift 2;;
        --dipp-ctl) CTL="$2"; shift 2;;
        --camera) CAM="$2"; shift 2;;
        --idle-window) IDLE="$2"; shift 2;;
        --ff-chunk) CHUNK="$2"; shift 2;;
        --ff-tick) TICK="$2"; shift 2;;
        --tail-ff) TAIL="$2"; shift 2;;
        *) echo "feed_trace: unknown option: $1" >&2; exit 2;;
    esac
done

die() { echo "feed_trace: $*" >&2; exit 2; }
now() { cut -d' ' -f1 /proc/uptime; }
calc() { awk "BEGIN { printf \"%.3f\", $* }"; }
gt() { awk "BEGIN { exit !(($1) > ($2)) }"; }

[ -n "$TRACE" ] || die "--trace is required"
[ -f "$TRACE" ] || die "trace not found: $TRACE"

# Validate the whole trace before anything is fed.
awk '
    /^[ \t]*#/ || NF == 0 { next }
    {
        n++
        if (NF != 6) { printf "line %d: expected 6 columns, got %d\n", NR, NF; bad = 1; exit }
        if ($1 !~ /^[0-9]+(\.[0-9]+)?$/) { printf "line %d: t_s not a non-negative number: %s\n", NR, $1; bad = 1; exit }
        if ($2 !~ /^[0-9]+(\.[0-9]+)?$/) { printf "line %d: ff_s not a non-negative number: %s\n", NR, $2; bad = 1; exit }
        if ($3 !~ /^-?[0-9]+$/) { printf "line %d: scene not an integer: %s\n", NR, $3; bad = 1; exit }
        if ($5 !~ /^[0-9]+$/ || $5 + 0 <= 0) { printf "line %d: slo_s not a positive integer: %s\n", NR, $5; bad = 1; exit }
        if ($6 != "urgent" && $6 != "routine") { printf "line %d: class not urgent|routine: %s\n", NR, $6; bad = 1; exit }
        if (n > 1 && $1 + 0 < prev) { printf "line %d: t_s decreases (%s after %s)\n", NR, $1, prev; bad = 1; exit }
        prev = $1 + 0
    }
    END { if (!bad && n == 0) { print "no frames in trace"; bad = 1 }; exit bad }
' "$TRACE" >&2 || die "invalid trace: $TRACE"

[ "$CHECK" -eq 1 ] && { echo "feed_trace: $TRACE ok"; exit 0; }

for pair in pipeline:PIPELINE images:IMAGES image:IMG console:CONSOLE feed-log:FEEDLOG \
            dipp-ctl:CTL camera:CAM idle-window:IDLE ff-chunk:CHUNK ff-tick:TICK tail-ff:TAIL; do
    eval "val=\${${pair#*:}}"
    [ -n "$val" ] || die "--${pair%%:*} is required"
done
gt "$CHUNK" 0 || die "--ff-chunk must be positive"
# The daemon re-checks a halted batch every 200 ms (HALT_POLL_US); a shorter
# tick would advance again before it has looked.
gt "$TICK" 0.2 || die "--ff-tick must exceed 0.2 s"
[ -x "$CTL" ] || die "dipp_ctl not executable: $CTL"
[ -x "$CAM" ] || die "camera not executable: $CAM"
[ -f "$IMG" ] || die "image not found: $IMG"
[ -f "$CONSOLE" ] || die "console log not found: $CONSOLE"


state() {
    grep '^SCHED_STATE' "$CONSOLE" | tail -n 1 | sed -n 's/.* state=\([a-z]*\).*/\1/p'
}

# Block until the daemon has been idle or halted for IDLE seconds. POSIX sh
# has no local variables, so these carry a wq_ prefix: an earlier version used
# `t`, overwrote the trace loop's own t_s, and logged a wall-clock value as the
# frame's trace time for every frame after a gap.
wait_quiescent() {
    wq_since=""
    while :; do
        wq_state=$(state); wq_now=$(now)
        case "$wq_state" in
            idle|halted)
                [ -n "$wq_since" ] || wq_since=$wq_now
                gt "$wq_now - $wq_since" "$IDLE - 0.0001" && return 0 ;;
            *) wq_since="" ;;
        esac
        sleep 1
    done
}

FF_SENT=0      # advance messages sent in total
ADV=0          # simulated seconds advanced in total

# Advance by $1 simulated seconds and count it.
advance() {
    "$CTL" advance "$1" > /dev/null || die "dipp_ctl advance $1 failed"
    FF_SENT=$((FF_SENT + 1))
    ADV=$(calc "$ADV + $1")
}

# Every advance must show up as a BATTERY_FF line before the next frame.
confirm_ff() {
    tries=0
    while [ "$(grep -c '^BATTERY_FF' "$CONSOLE")" -lt "$FF_SENT" ]; do
        tries=$((tries + 1))
        [ "$tries" -le 300 ] || die "daemon applied $(grep -c '^BATTERY_FF' "$CONSOLE") of $FF_SENT advances after 60 s"
        sleep 0.2
    done
}

T0=$(now)
simnow() { calc "$(now) - $T0 + $ADV"; }

: > "$FEEDLOG"
echo "TRACE_START wall=$T0 trace=$TRACE" >> "$FEEDLOG"
FFCUM=0; N=0; LATE=0

grep -v '^[[:space:]]*#' "$TRACE" | grep -v '^[[:space:]]*$' > /tmp/feed_trace.$$
while read -r t ff scene datatake slo cls; do
    FFCUM=$(calc "$FFCUM + $ff")
    target=$(calc "$t + $FFCUM")
    if gt "$ff" 0; then
        q0=$(now)
        wait_quiescent
        qw=$(calc "$(now) - $q0")
        a0=$ADV; chunks=0
        while :; do
            rem=$(calc "$target - $(simnow)")
            gt "$rem" 0.001 || break
            c=$(awk "BEGIN { r = $rem; c = $CHUNK; printf \"%.3f\", (r < c ? r : c) }")
            advance "$c"; chunks=$((chunks + 1))
            sleep "$TICK"
        done
        confirm_ff
        echo "FF scene=$scene ff_s=$ff advanced_s=$(calc "$ADV - $a0") chunks=$chunks quiescent_wait_s=$qw state=$(state)" >> "$FEEDLOG"
    fi
    w=$(calc "$target - $(simnow)")
    gt "$w" 0 && sleep "$w"
    late=$(calc "$(simnow) - $target")
    gt "$late" 1.0 && LATE=$((LATE + 1))
    "$CTL" mark "$scene" > /dev/null || die "dipp_ctl mark $scene failed"
    "$CAM" "$IMAGES" "$PIPELINE" 123 "$IMG" "$slo" > /dev/null 2>&1 || die "camera failed on scene $scene"
    N=$((N + 1))
    echo "FEED scene=$scene datatake=$datatake class=$cls slo_s=$slo t_s=$t target_sim_s=$target sim_now_s=$(simnow) late_s=$late wall=$(now)" >> "$FEEDLOG"
done < /tmp/feed_trace.$$
rm -f /tmp/feed_trace.$$

# Let the backlog finish: fast-forward while halted, up to TAIL seconds.
TAIL_ADV=0
if gt "$TAIL" 0; then
    wait_quiescent
    while [ "$(state)" = halted ] && gt "$TAIL" "$TAIL_ADV"; do
        c=$(awk "BEGIN { r = $TAIL - $TAIL_ADV; c = $CHUNK; printf \"%.3f\", (r < c ? r : c) }")
        advance "$c"; TAIL_ADV=$(calc "$TAIL_ADV + $c")
        sleep "$TICK"
        [ "$(state)" = busy ] && wait_quiescent
    done
    confirm_ff
fi
echo "TRACE_END frames=$N late_frames=$LATE advanced_s=$ADV tail_advanced_s=$TAIL_ADV end_state=$(state) wall=$(now)" >> "$FEEDLOG"
echo "feed_trace: $N frames, $LATE late, ${ADV}s fast-forwarded (${TAIL_ADV}s in the tail), daemon $(state)"
