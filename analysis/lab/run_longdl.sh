#!/bin/sh
# Long-deadline / urgent-deadline check on FRESH random scenarios (seeds 200000+,
# never used for selection or quoted), queue capacity 100 and 300 (2026-10-13).
cd "$(dirname "$0")/../.."
for cap in 100 300; do
  for fx in "slo_r=21600" "slo_u=10" "slo_u=60"; do
    o=analysis/lab/fresh_$(echo $fx | tr '=' '_')_cap$cap.csv
    python3 -c "
import sys; sys.path.insert(0,'tools'); import policy_lab as L, sched_sim as S
L.MAX_INGEST = S.MAX_INGEST = $cap
sys.argv = ['policy_lab.py','sweep','-n','1500','--seed0','200000','--set','sens2','--fix','$fx','-o','$o']
L.main()"
  done
done
echo ALLDONE
