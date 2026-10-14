#!/bin/sh
# Operating-envelope sampler (R1.5), run ON THE BOARD next to a bench run:
#
#   sh envelope_sampler.sh <interval_s> <out_file> &     # stop: touch <out_file>.stop
#
# One line per sample:
#   t_s pss_kb_dipp n_dipp shm_bytes shm_segs data_bytes data_files mem_avail_kb
# pss_kb_dipp sums Pss over every process named dipp (the daemon and its
# forked module children share the name); shm_* is every SysV segment on the
# board (SIPP is the only user during a run); data_* is SIPP's persisted
# frames and checkpoints. Summarise host-side with analysis/envelope_summary.py.
# Writes nothing to the console; read the file after the run.
set -u
IVL="$1"; OUT="$2"
DATA=/usr/share/dipp/data
echo "# t_s pss_kb_dipp n_dipp shm_bytes shm_segs data_bytes data_files mem_avail_kb" > "$OUT"
T0=$(cut -d' ' -f1 /proc/uptime)
while [ ! -e "$OUT.stop" ]; do
    T=$(cut -d' ' -f1 /proc/uptime)
    PSS=0; N=0
    for p in $(pidof dipp); do
        v=$(awk '/^Pss:/{print $2; exit}' /proc/$p/smaps_rollup 2>/dev/null)
        [ -n "$v" ] && PSS=$((PSS + v)) && N=$((N + 1))
    done
    SHM=$(awk 'NR>1{b+=$4; n++} END{print b+0, n+0}' /proc/sysvipc/shm)
    DB=$(du -sk "$DATA" 2>/dev/null | cut -f1); DB=$(( ${DB:-0} * 1024 ))
    DF=$(ls "$DATA" 2>/dev/null | wc -l)
    MA=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)
    echo "$(awk -v a="$T" -v b="$T0" 'BEGIN{printf "%.1f", a-b}') $PSS $N $SHM $DB $DF $MA" >> "$OUT"
    sleep "$IVL"
done
rm -f "$OUT.stop"
