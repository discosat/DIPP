#!/bin/sh
# Parameter sensitivity of policy v2 (held-out seeds, quoted once).
cd "$(dirname "$0")/../.."
for f in none window=4 window=16 over_err=0.5 over_err=1.5; do
  if [ "$f" = none ]; then fx=""; o=base; else fx="--fix $f"; o=$(echo $f | tr '=' '_'); fi
  python3 tools/policy_lab.py sweep -n 1500 --seed0 100000 --set sens2 $fx -o analysis/lab/sens2_$o.csv
done
echo ALLDONE
