#!/bin/sh
cd "$(dirname "$0")/../.."
python3 tools/policy_lab.py grid --set fam7 -o analysis/lab/grid_fam7.csv
python3 tools/policy_lab.py sweep -n 2000 --set fam7 -o analysis/lab/fam7_sel.csv
echo ALLDONE
