# Flight models

The TFLite models the board loads from /usr/share/models/ (see the
`model_path` entries in ../flight-config/module_*.yaml).

| File | Module | Source |
|---|---|---|
| landcover_ms_low.tflite | Land-cover classification, low effort | training/out (Stage 4.5) |
| landcover_ms_medop.tflite | Land-cover classification, medium effort | training/out (Stage 4.5) |
| landcover_ms_high.tflite | Land-cover classification, high effort | training/out (Stage 4.5) |

Not yet here: the three cloud-segmentation models
(cs12g050_p_w08d3_r096, cs12g050q_g_a035_r160, cs12g050q_g_a14_r224). They
are deployed on the board but not found on the development host; copy them
from the board's /usr/share/models/ to complete this folder.

Training checkpoints and logs (training/out/) are not versioned.
