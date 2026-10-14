#!/bin/sh
# Host test for tests/board/feed_trace.sh, with a stub daemon.
#
#   sh tests/test_feed_trace.sh        (from the repository root; ~40 s)
#
# The stubs stand in for the board: dipp_ctl appends the BATTERY_FF / MARK
# lines the daemon would log and records every call; the camera records its
# arguments; SCHED_STATE lines are written into the fake console to play the
# daemon's state. What is checked is the feeder's own contract: frames land
# at their simulated times, gaps are fast-forwarded only when the daemon is
# quiescent and never past the next frame, the tail stops when the daemon
# goes idle, every SLO reaches the camera, and anything malformed is refused
# before a single frame is fed.
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
FEED="$HERE/tests/board/feed_trace.sh"
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
FAIL=0
check() { if eval "$2"; then printf '  %-62s PASS\n' "$1"; else printf '  %-62s FAIL\n' "$1"; FAIL=$((FAIL + 1)); fi; }
num() { awk "BEGIN { exit !($*) }"; }

cat > "$W/dipp_ctl" <<EOF
#!/bin/sh
echo "\$*" >> "$W/ctl.log"
case "\$1" in
  advance)
    echo "BATTERY_FF adv_s=\$2" >> "$W/console.log"
    # A stub backlog: the daemon goes idle once 20 s have been advanced in
    # the tail (marker file set by the test).
    if [ -f "$W/tail_mode" ]; then
      echo "\$2" >> "$W/tail_adv"
      t=\$(awk '{ s += \$1 } END { printf "%.3f", s }' "$W/tail_adv")
      if awk "BEGIN { exit !(\$t >= 20) }"; then echo "SCHED_STATE state=idle" >> "$W/console.log"; fi
    fi
    exit 0 ;;
  mark) echo "MARK scene=\$2" >> "$W/console.log" ;;
esac
EOF
cat > "$W/camera" <<EOF
#!/bin/sh
echo "\$*" >> "$W/cam.log"
EOF
chmod +x "$W/dipp_ctl" "$W/camera"
: > "$W/image.bayerRG"

run() {
    sh "$FEED" --trace "$1" --pipeline 1 --images 1 --image "$W/image.bayerRG" \
       --console "$W/console.log" --feed-log "$W/feed.log" --dipp-ctl "$W/dipp_ctl" \
       --camera "$W/camera" --idle-window 1 --ff-chunk 10 --ff-tick 0.3 --tail-ff "${2:-0}"
}
reset() { rm -f "$W/ctl.log" "$W/cam.log" "$W/tail_mode" "$W/tail_adv"; echo "SCHED_STATE state=idle" > "$W/console.log"; }

echo "feed_trace.sh"

# 1. A gap of 30 s compressed between frames 2 and 3.
cat > "$W/t1.trace" <<EOF
# sipp-trace v1
0 0 1 DT1 15 urgent
2 0 2 DT1 300 routine
3 30 3 DT2 300 routine
4 0 4 DT2 15 urgent
EOF
reset
run "$W/t1.trace" > /dev/null 2>&1; rc=$?
check "a valid trace runs to the end" '[ $rc -eq 0 ] && grep -q "^TRACE_END frames=4" "$W/feed.log"'
check "frames are fed in order, each marked first" \
      '[ "$(grep -o "^mark [0-9]*" "$W/ctl.log" | tr "\n" " ")" = "mark 1 mark 2 mark 3 mark 4 " ]'
check "every frame reaches the camera with its own SLO" \
      '[ "$(awk "{print \$5}" "$W/cam.log" | tr "\n" " ")" = "15 300 300 15 " ]'
late_max=$(sed -n 's/^FEED .* late_s=\([0-9.-]*\) .*/\1/p' "$W/feed.log" | sort -n | tail -n 1)
check "every frame lands within 0.5 s of its simulated time (max ${late_max})" 'num "$late_max < 0.5"'
adv=$(sed -n 's/^TRACE_END .* advanced_s=\([0-9.]*\) .*/\1/p' "$W/feed.log")
# Frame 3's simulated time is 3 + 30 = 33 s; the wall time spent reaching it
# (~3 s of trace plus the 1 s idle window) is not fast-forwarded again.
check "the gap is fast-forwarded by 30 s less the wall time already spent (${adv} s)" \
      'num "$adv > 27.5 && $adv < 30.01"'
check "feed.log records each frame's own trace time, also after a gap" \
      '[ "$(sed -n "s/^FEED .* t_s=\([0-9.]*\) .*/\1/p" "$W/feed.log" | tr "\n" " ")" = "0 2 3 4 " ]'
check "in chunks of at most 10 s" \
      '[ "$(grep -c "^advance" "$W/ctl.log")" -ge 3 ] && ! awk "/^advance/ && \$2 > 10.0001 { exit 0 } END { exit 1 }" "$W/ctl.log"'
check "every advance was confirmed by a BATTERY_FF line" \
      '[ "$(grep -c "^BATTERY_FF" "$W/console.log")" -eq "$(grep -c "^advance" "$W/ctl.log")" ]'

# 2. A busy daemon holds the fast-forward back until it is quiescent.
cat > "$W/t2.trace" <<EOF
0 0 1 DT1 300 routine
1 20 2 DT2 300 routine
EOF
reset
echo "SCHED_STATE state=busy" >> "$W/console.log"
( sleep 4; echo "SCHED_STATE state=halted" >> "$W/console.log" ) &
run "$W/t2.trace" > /dev/null 2>&1
qw=$(sed -n 's/^FF .* quiescent_wait_s=\([0-9.]*\) .*/\1/p' "$W/feed.log")
check "no fast-forward while busy; halted counts as quiescent (waited ${qw} s)" 'num "$qw >= 4.5"'

# 2b. A daemon busy for the whole gap: the gap elapses in real time, nothing
#     is fast-forwarded, and the frames after it keep their own spacing
#     (2026-10-09: the wait used to be unbounded, and every overdue frame was
#     then fed at once).
cat > "$W/t2b.trace" <<EOF
0 0 1 DT1 300 routine
1 5 2 DT2 300 routine
4 0 3 DT2 300 routine
EOF
reset
echo "SCHED_STATE state=busy" >> "$W/console.log"
( sleep 15; echo "SCHED_STATE state=idle" >> "$W/console.log" ) &
run "$W/t2b.trace" > /dev/null 2>&1
late2=$(sed -n 's/^FEED scene=2 .* late_s=\([0-9.-]*\) .*/\1/p' "$W/feed.log")
late3=$(sed -n 's/^FEED scene=3 .* late_s=\([0-9.-]*\) .*/\1/p' "$W/feed.log")
adv2=$(sed -n 's/^FF .* advanced_s=\([0-9.]*\) .*/\1/p' "$W/feed.log")
check "busy through a gap: the next frame is fed when due, not after the backlog (late ${late2} s)" 'num "$late2 < 1.5"'
check "busy through a gap: nothing is fast-forwarded (${adv2} s) and it is logged as not quiescent" \
      'num "$adv2 == 0" && grep -q "^FF .* quiescent=0" "$W/feed.log"'
check "busy through a gap: the frame after it keeps its spacing (late ${late3} s)" 'num "$late3 < 0.5"'
wait

# 3. The tail: advance while halted, stop once the daemon is idle.
cat > "$W/t3.trace" <<EOF
0 0 1 DT1 300 routine
EOF
reset
echo "SCHED_STATE state=halted" >> "$W/console.log"
touch "$W/tail_mode"
run "$W/t3.trace" 100 > /dev/null 2>&1
tail_adv=$(sed -n 's/^TRACE_END .* tail_advanced_s=\([0-9.]*\) .*/\1/p' "$W/feed.log")
check "the tail stops once the backlog is done, not at its cap (${tail_adv} s of 100)" \
      'num "$tail_adv >= 20 && $tail_adv <= 30" && grep -q "end_state=idle" "$W/feed.log"'

# 4. Refusals: nothing is fed from a malformed trace or with a missing argument.
bad() {
    printf '%s\n' "$2" > "$W/bad.trace"
    reset
    run "$W/bad.trace" > /dev/null 2>&1; rc=$?
    check "$1" '[ $rc -eq 2 ] && [ ! -s "$W/cam.log" ]'
}
bad "refuses 5 columns"               "0 0 1 DT1 300"
bad "refuses a class other than urgent|routine" "0 0 1 DT1 300 critical"
bad "refuses a non-integer SLO"       "0 0 1 DT1 1.5 urgent"
bad "refuses SLO 0"                   "0 0 1 DT1 0 urgent"
bad "refuses a negative ff"           "0 -5 1 DT1 300 routine"
bad "refuses t_s going backwards"     "$(printf '5 0 1 DT1 300 routine\n4 0 2 DT1 300 routine')"
bad "refuses a trace with no frames"  "# only a header"
reset
sh "$FEED" --trace "$W/t1.trace" --pipeline 1 > /dev/null 2>&1; rc=$?
check "refuses a missing argument (no defaults)" '[ $rc -eq 2 ] && [ ! -e "$W/cam.log" ]'
sh "$FEED" --check --trace "$W/t1.trace" > /dev/null 2>&1; rc=$?
check "--check validates a trace without feeding" '[ $rc -eq 0 ] && [ ! -e "$W/cam.log" ]'

# 5. --image-list: frame k gets line k mod N.
: > "$W/a.bayerRG"; : > "$W/b.bayerRG"
printf '%s\n%s\n' "$W/a.bayerRG" "$W/b.bayerRG" > "$W/images.txt"
cat > "$W/t5.trace" <<EOF
0 0 1 DT1 300 routine
1 0 2 DT1 300 routine
2 0 3 DT1 300 routine
EOF
reset
sh "$FEED" --trace "$W/t5.trace" --pipeline 1 --images 1 --image-list "$W/images.txt" \
   --console "$W/console.log" --feed-log "$W/feed.log" --dipp-ctl "$W/dipp_ctl" \
   --camera "$W/camera" --idle-window 1 --ff-chunk 10 --ff-tick 0.3 --tail-ff 0 > /dev/null 2>&1; rc=$?
imgs=$(awk '{ print $4 }' "$W/cam.log" | sed 's#.*/##' | tr '\n' ' ')
check "--image-list rotates a, b, a (got: $imgs)" '[ $rc -eq 0 ] && [ "$imgs" = "a.bayerRG b.bayerRG a.bayerRG " ]'
check "feed.log records each frame's image" '[ "$(grep -c "image=a.bayerRG" "$W/feed.log")" -eq 2 ]'
printf '%s\n' "$W/missing.bayerRG" > "$W/images_bad.txt"
reset
sh "$FEED" --trace "$W/t5.trace" --pipeline 1 --images 1 --image-list "$W/images_bad.txt" \
   --console "$W/console.log" --feed-log "$W/feed.log" --dipp-ctl "$W/dipp_ctl" \
   --camera "$W/camera" --idle-window 1 --ff-chunk 10 --ff-tick 0.3 --tail-ff 0 > /dev/null 2>&1; rc=$?
check "refuses a list naming a missing image, before feeding" '[ $rc -eq 2 ] && [ ! -e "$W/cam.log" ]'
reset
sh "$FEED" --trace "$W/t5.trace" --pipeline 1 --images 1 --image "$W/a.bayerRG" --image-list "$W/images.txt" \
   --console "$W/console.log" --feed-log "$W/feed.log" --dipp-ctl "$W/dipp_ctl" \
   --camera "$W/camera" --idle-window 1 --ff-chunk 10 --ff-tick 0.3 --tail-ff 0 > /dev/null 2>&1; rc=$?
check "refuses --image with --image-list" '[ $rc -eq 2 ] && [ ! -e "$W/cam.log" ]'

[ "$FAIL" -eq 0 ] && echo "OK (0 failures)" || echo "FAILED ($FAIL failures)"
exit "$FAIL"
