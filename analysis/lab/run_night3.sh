#!/bin/sh
cd "$(dirname "$0")/../.."
python3 tools/policy_lab.py sweep -n 1500 --seed0 100000 --set c3 --fix slo_u=10 -o analysis/lab/slou_10.csv
python3 tools/policy_lab.py sweep -n 1500 --seed0 100000 --set c3 --fix slo_u=40 -o analysis/lab/slou_40.csv
python3 tools/policy_lab.py sweep -n 600 --seed0 100000 --set c3 --fix frames_target=3000 -o analysis/lab/long3000.csv
echo ALLDONE
