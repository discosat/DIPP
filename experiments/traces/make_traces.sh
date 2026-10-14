#!/bin/sh
# The campaign's arrival traces, regenerated from fixed inputs:
#   sh experiments/traces/make_traces.sh
# Inputs: analysis/s2-workload/*/windows.json (tools/s2_acquisition.py over
# ESA's KML plans, ~/dipp-datasets/s2-acq, SHA-256 in characterisation.json),
# the AOIs and targets.geojson, the DISCO-II platform file, and
# analysis/cost_model.json (the urgent-SLO feasibility check).
#
# Scenarios (agreed 2026-09-30/10-01; urgent SLO 20 s from 2026-10-03, see the
# session plan: 15 s is below the 17.83 s EDF worst case):
#   main     one satellite (S2A) over global land, 2026-10-04 bursts 5-6: two
#            consecutive orbits over Europe/Africa, 130 + 92 frames; urgent =
#            footprint contains a synthetic target, 40 / Mkm^2 (~10 %),
#            three target scatters (seeds 1-3)
#   urgentN  the same frames at 0 and 265 / Mkm^2 (0 % and ~50 % urgent)
#   eg       East Greenland, S2A, 2026-10-11 (38 frames) with the real target
#            sites -- the low-load discussion case
#   con      the constellation (S2A+B+C) over global land, 2026-10-04 burst 11:
#            289 frames in 30 min -- stress
#   poi3     Poisson, 3 s mean, 100 frames -- the old stress setting
#   poi76    Poisson at S2A's global-land mean rate (1131 frames/day -> 76 s),
#            222 frames, the main trace's start phase: same load, no bursts
set -eu
cd "$(dirname "$0")/../.."
OUT=experiments/traces
GEN=tools/make_arrival_trace.py
COMMON="--platform experiments/platforms/disco2-bol.env --profile-dir analysis/power-profile \
  --realtime-max-gap-s 60 --slo-urgent 20 --slo-routine 300 \
  --cost-model analysis/cost_model.json --manifest experiments/board/flight-config/manifest.yaml \
  --pipelines 1,2,3,4,6"
FRAME="--frame-period-s 6.68 --frame-along-km 46.73 --frame-across-km 56"
GL=analysis/s2-workload/global_land/windows.json
GLAOI=analysis/s2-workload/aoi_global_land.geojson

for s in 1 2 3; do
  python3 $GEN s2 --windows $GL --satellite S2A --merge-gap-s 600 --day 2026-10-04 --days 1 \
    --first-burst 5 --n-bursts 2 --synthetic-targets 40 --target-aoi $GLAOI --seed $s \
    $FRAME $COMMON -o $OUT/main-s$s.trace
done
# Load axis (2026-10-07): the same real bursts at 2x frame density, i.e. 50 %
# along-track frame overlap (a planned acquisition parameter, known in advance).
for s in 1 2 3; do
  python3 $GEN s2 --windows $GL --satellite S2A --merge-gap-s 600 --day 2026-10-04 --days 1 \
    --first-burst 5 --n-bursts 2 --synthetic-targets 40 --target-aoi $GLAOI --seed $s \
    --frame-period-s 3.34 --frame-along-km 46.73 --frame-across-km 56 $COMMON -o $OUT/main2x-s$s.trace
done
for d in 0 265; do
  python3 $GEN s2 --windows $GL --satellite S2A --merge-gap-s 600 --day 2026-10-04 --days 1 \
    --first-burst 5 --n-bursts 2 --synthetic-targets $d --target-aoi $GLAOI --seed 1 \
    $FRAME $COMMON -o $OUT/main-urgent$d-s1.trace
done
python3 $GEN s2 --windows analysis/s2-workload/east_greenland/windows.json --satellite S2A \
  --merge-gap-s 600 --day 2026-10-11 --days 1 --first-burst 0 --n-bursts 1 \
  --targets analysis/s2-workload/targets.geojson --seed 1 $FRAME $COMMON -o $OUT/eg.trace
python3 $GEN s2 --windows $GL --satellite all --merge-gap-s 600 --day 2026-10-04 --days 1 \
  --first-burst 11 --n-bursts 1 --synthetic-targets 40 --target-aoi $GLAOI --seed 1 \
  $FRAME $COMMON -o $OUT/con.trace
PH=$(sed -n 's/^# phase0=//p' $OUT/main-s1.trace)
python3 $GEN poisson --mean-s 3 --n 100 --urgent-fraction 0.1 --phase0 "$PH" --seed 1 \
  $COMMON -o $OUT/poi3.trace
python3 $GEN poisson --mean-s 76 --n 222 --urgent-fraction 0.1 --phase0 "$PH" --seed 1 \
  $COMMON -o $OUT/poi76.trace
for f in $OUT/*.trace; do sh tests/board/feed_trace.sh --check --trace "$f"; done
