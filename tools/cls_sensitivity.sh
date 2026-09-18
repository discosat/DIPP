#!/bin/bash
# Stage 4.5's actual exit criterion: do a sea scene and an urban scene produce
# different, plausible classifications on hardware?
#
# This has never passed. The original symptom was that class 9 (SeaLake) never
# appeared at all in a coastal Copenhagen scene. The cause turned out to be the
# rendering, not the models: the device applied gain 0.0403/0.0314/0.0252 with
# offsets 17/37/61, while the EuroSAT imagery the models learned is a uniform
# `reflectance x 0.093` with no offset. Too dark, and blue-shifted at low
# signal, so vegetation looked like water. Both are now config
# (module_1 gain/offset, train_res=64 on slots 3/4/5).
#
# Expected if the fix holds: koge-coast (sea) leans SeaLake/River, amager
# (urban) leans Residential/Industrial, and the two histograms differ. Equal
# histograms mean the pipeline is still input-insensitive.
set -u
cd "$(dirname "$0")/.." || exit 1
export BOARD_TTY="${BOARD_TTY:-/dev/ttyUSB1}"
OUT=analysis/stage4.5-models/dev_cls_sensitivity.json
for img in koge-coast-no-clouds amager-no-clouds; do
  for m in custom_conv_model_landcover qat_a050r224 qat_a140r224; do
    echo "=== $img / $m ==="
    ./tools/bench_models.py --kind classification --n 1 --min-drain 120 \
        --image "$img.bayerRG" --models "$m" -o "$OUT"
    ./tools/board_console.py "L=\$(ls -t /home/root/logs/tflite_classification_* 2>/dev/null | head -n 1); \
      grep -ao 'Top class: [0-9]*' \$L | sort | uniq -c | sort -rn | head -n 4 | \
      awk '{printf \"    class %s : %s tiles\n\", \$4, \$1}'"
  done
done
echo "=== ALL DONE ==="
