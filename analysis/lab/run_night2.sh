#!/bin/sh
cd "$(dirname "$0")/../.."
for cv in 0.3 1.0; do
  python3 tools/policy_lab.py sweep -n 2000 --seed0 100000 --set c3 --fix arrival_cv=$cv -o analysis/lab/arrcv_$cv.csv
done
echo ALLDONE
