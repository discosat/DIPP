#!/bin/sh
cd "$(dirname "$0")/../.."
until grep -q "^wrote" analysis/lab/grid_final.log; do sleep 10; done
for f in window=4 window=16 over_err=0.5 over_err=1.5; do
  python3 tools/policy_lab.py sweep -n 1500 --seed0 100000 --set sens --fix $f -o analysis/lab/sens_$(echo $f | tr '=' '_').csv
done
echo ALLDONE
