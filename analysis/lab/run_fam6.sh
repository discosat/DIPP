#!/bin/sh
cd "$(dirname "$0")/../.."
until grep -q "ALLDONE" analysis/lab/sens.log; do sleep 10; done
python3 tools/policy_lab.py grid --set fam6 -o analysis/lab/grid_fam6.csv
python3 tools/policy_lab.py sweep -n 2000 --set fam6 -o analysis/lab/fam6_sel.csv
echo ALLDONE
