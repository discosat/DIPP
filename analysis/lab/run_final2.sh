#!/bin/sh
cd "$(dirname "$0")/../.."
python3 tools/policy_lab.py sweep -n 3000 --seed0 100000 --set final2 -o analysis/lab/test2_exact.csv
python3 tools/policy_lab.py sweep -n 3000 --seed0 100000 --set final2 --fix model_err=0.3,jitter=0.15 -o analysis/lab/test2_err03.csv
python3 tools/policy_lab.py sweep -n 3000 --seed0 100000 --set final2 --fix model_err=0.7,jitter=0.2 -o analysis/lab/test2_err07.csv
python3 analysis/lab_real.py --set final2 -o analysis/lab/real_final2.csv
echo ALLDONE
