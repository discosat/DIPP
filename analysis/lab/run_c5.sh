#!/bin/sh
cd "$(dirname "$0")/../.."
python3 tools/policy_lab.py sweep -n 2000 --set c4c5 -o analysis/lab/c5_sel.csv
python3 tools/policy_lab.py grid --set c4c5 -o analysis/lab/c5_grid.csv
python3 analysis/lab_real.py --set c4c5 -o analysis/lab/c5_real.csv > analysis/lab/c5_real.log 2>&1
echo ALLDONE
