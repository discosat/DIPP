#!/bin/sh
# 2026-10-13: capacity-aware budget and queue-fill horizon (selection on fresh seeds 200000+).
cd "$(dirname "$0")/../.."
python3 analysis/lab_deadline.py --set capfix -o analysis/lab/deadline_capfix.csv -j 7
for fx in none slo_r=21600; do
  o=analysis/lab/sel200k_capfix_$(echo $fx | tr '=' '_').csv
  if [ $fx = none ]; then F=""; else F="--fix $fx"; fi
  python3 tools/policy_lab.py sweep -n 1500 --seed0 200000 --set capfix $F -o $o
done
echo ALLDONE
