#!/bin/sh
# Final simulation evidence for the FROZEN policy (2026-10-14, paper-rev/policy_final.md):
# set final3 ("sipp" = v2 + capacity budget + fill horizon + energy-timed check).
# Quote-once seed ranges, never used for selection: random 300000+, grid 400000+,
# random energy 500000+, adversary random starts 600000+.
set -e
cd "$(dirname "$0")/../.."
O=analysis/lab/final3
mkdir -p $O
lab() { python3 tools/policy_lab.py "$@"; }
echo "== held-out random"; lab sweep -n 3000 --seed0 300000 --set final3 -o $O/heldout.csv
echo "== sensitivity";     lab sweep -n 1500 --seed0 300000 --set final3-sens -o $O/sens_base.csv
for f in window=4 window=16 over_err=0.5 over_err=1.5; do
  lab sweep -n 1500 --seed0 300000 --set final3-sens --fix $f -o $O/sens_$(echo $f | tr '=' '_').csv; done
echo "== arrivals";        for cv in 0.3 1.0; do lab sweep -n 1500 --seed0 300000 --set final3 --fix arrival_cv=$cv -o $O/arrcv_$cv.csv; done
echo "== deadlines";       for f in slo_r=21600 slo_u=10 slo_u=60; do
  lab sweep -n 1500 --seed0 300000 --set final3 --fix $f -o $O/fix_$(echo $f | tr '=' '_').csv; done
echo "== grid";            lab grid --set final3 --seed0 400000 --long -o $O/grid.csv
echo "== energy random";   lab sweep -n 2000 --seed0 500000 --set final3-energy --fix energy=1 -o $O/energy_random.csv
echo "== adversary";       python3 analysis/lab_adversary.py --policy sipp --set final3 --slo-max 21600 \
                             --start $O/heldout.csv --chains 32 --iters 60 -o $O/adversary.csv
echo "== real traces: deadline sweep"; python3 analysis/lab_deadline.py --set final3 -o $O/deadline.csv -j 7
echo "== real traces: high-cost scaling"; python3 analysis/lab_high_scaling.py --set final3 --policy sipp -o $O/high_scaling.csv
echo "== real traces: energy regimes"; LAB_ENERGY_SET=final3-energy python3 analysis/lab_energy_deficit.py -o $O/energy_deficit.csv -j 7
echo ALLDONE
