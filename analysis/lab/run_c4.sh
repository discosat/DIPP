#!/bin/sh
cd "$(dirname "$0")/../.."
python3 tools/policy_lab.py sweep -n 2000 --set c3c4 -o analysis/lab/c4_sel.csv
python3 tools/policy_lab.py sweep -n 3000 --seed0 100000 --set c3c4 -o analysis/lab/c4_test.csv
python3 tools/policy_lab.py grid --set c3c4 -o analysis/lab/c4_grid.csv
python3 tools/policy_lab.py sweep -n 1000 --seed0 100000 --set c3c4 --fix urgent=1.0 -o analysis/lab/c4_allurgent.csv
echo ALLDONE
