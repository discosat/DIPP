#!/bin/sh
# Final evaluation analysis: merge eval (v1, valid runs) + eval2 (v2, re-runs),
# parse, compute utility/regret, Table VII and figures.
#   sh analysis/run_final_analysis.sh
set -e
cd "$(dirname "$0")/.."
python3 analysis/eval_merge.py -o runs/eval_final
python3 analysis/parse_runs.py runs/eval_final/*/ -o analysis/parsed/eval_final > analysis/parsed/eval_final.parse.log 2>&1
python3 analysis/campaign_utility.py analysis/parsed/eval_final -o analysis/eval_final
python3 analysis/eval_report.py analysis/parsed/eval_final analysis/eval_final -o analysis/eval_final --cdf-cells C3,C4,C5
echo "done: analysis/eval_final/{runs,cells}.csv, summary.md, table7.tex, U_by_cell.pdf, cdf_*.pdf"
