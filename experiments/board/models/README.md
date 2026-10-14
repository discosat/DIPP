# Flight models

The TFLite models the board loads from /usr/share/models/ (see the
`model_path` entries in ../flight-config/module_*.yaml).

| File | Module | Bytes | md5 | Source |
|---|---|---|---|---|
| landcover_ms_low.tflite | Land-cover classification, low effort | 31,464 | cfdd37dcb258dd1b8a8ecb392cd741fe | training/out (Stage 4.5) |
| landcover_ms_medop.tflite | Land-cover classification, medium effort | 978,976 | 56888082b5bab0a7235055c9b78d4cda | training/out (Stage 4.5) |
| landcover_ms_high.tflite | Land-cover classification, high effort | 4,993,992 | b956eae948ed87fb4fae0a56eb5f9f71 | training/out (Stage 4.5) |
| cs12g050_p_w08d3_r096.tflite | Cloud segmentation, low effort | 72,792 | b437d54dff33dabc6c7daf6606402323 | copied from the board's /usr/share/models/ (2026-10-14) |
| cs12g050q_g_a035_r160.tflite | Cloud segmentation, medium effort | 1,390,216 | a15cec5f238576e69a62c9fbe318a239 | copied from the board's /usr/share/models/ (2026-10-14) |
| cs12g050q_g_a14_r224.tflite | Cloud segmentation, high effort | 7,394,544 | 43400500f9f9b07e0c84e0798037dcdb | copied from the board's /usr/share/models/ (2026-10-14) |

The cloud-segmentation models were copied from the board (USB stick and,
for the smallest, tools/fetch_board_models.py over the console) and match
the board's md5. Training checkpoints and logs (training/out/) are not
versioned.
