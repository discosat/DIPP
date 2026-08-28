| Stage | Effort | slot | MEM median ms | MEM IQR | n (exec/runs) | MMAP median ms | MMAP IQR | n (exec/runs) | Overhead |
|---|---|---|---|---|---|---|---|---|---|
| libdemosaic | default | 1 | 365.4 | 3.9 | 4/1 | 912.3 | 11.5 | 4/1 | +149.7% (CI +525.48..+560.95 ms) |
| libtile | default | 2 | 154.5 | 2.8 | 4/1 | 429.6 | 4.7 | 4/1 | +178.0% (CI +266.87..+281.58 ms) |
| libtflite_classification | low | 3 | 245.9 | 2.4 | 4/1 | 259.1 | 3.3 | 4/1 | +5.4% (CI +9.54..+21.51 ms) |
| libtflite_binary_segmentation | low | 6 | 91.2 | 1.2 | 4/1 | 109.8 | 5.3 | 4/1 | +20.4% (CI +15.58..+24.85 ms) |
| libjpegxl_module | low | 11 | 152.8 | 2.7 | 4/1 | 170.5 | 3.6 | 4/1 | +11.6% (CI +11.49..+21.19 ms) |
