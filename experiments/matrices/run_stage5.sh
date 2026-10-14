#!/bin/sh
# Run the Stage 5 campaigns in priority order, one after another.
#   setsid nohup sh experiments/matrices/run_stage5.sh > runs/stage5.log 2>&1 &
# Each campaign resumes from its first incomplete run (campaign.py skips
# complete runs), so re-invoking this after any stop repeats nothing.
cd "$(dirname "$0")/../.." || exit 1
for m in table7-core arm-board arm-load arm-reserve arm-eol fig-sensitivity; do
    echo "=== $(date -u +%FT%TZ) $m ==="
    python3 tools/campaign.py "experiments/matrices/$m.yaml" || echo "=== $m: campaign.py exited $? ==="
done
echo "=== $(date -u +%FT%TZ) all done ==="
