#!/bin/sh
cd "$(dirname "$0")/../.."
python3 tools/policy_lab.py sweep -n 2000 --set energy --fix energy=1 -o analysis/lab/energy_sel.csv
echo ALLDONE
