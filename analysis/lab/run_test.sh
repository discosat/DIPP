#!/bin/sh
# Held-out test (seeds 100000+), three model-error conditions, sequential.
cd "$(dirname "$0")/../.."
python3 tools/policy_lab.py sweep -n 3000 --seed0 100000 --set final -o analysis/lab/test_exact.csv
python3 tools/policy_lab.py sweep -n 3000 --seed0 100000 --set final --fix model_err=0.3,jitter=0.15 -o analysis/lab/test_err03.csv
python3 tools/policy_lab.py sweep -n 3000 --seed0 100000 --set final --fix model_err=0.7,jitter=0.2 -o analysis/lab/test_err07.csv
echo ALLDONE
