## Table V (corrected) -- module sizes for uplink

| Module | Raw | Stripped | +Brotli | Brotli/raw | Uplink pass |
|---|---:|---:|---:|---:|---:|
| `demosaic` | 427,544 | 30,696 | 9,401 | 2.2% | 1.6% |
| `tile` | 343,520 | 34,792 | 11,047 | 3.2% | 1.9% |
| `tflite_classification` | 2,109,856 | 47,088 | 14,083 | 0.7% | 2.4% |
| `tflite_binary_segmentation` | 2,107,688 | 47,088 | 14,013 | 0.7% | 2.4% |
| `jpegxl_encode` | 426,632 | 38,888 | 11,858 | 2.8% | 2.1% |
| `single_module` | 2,487,664 | 59,376 | 18,260 | 0.7% | 3.2% |
| `resize` | 392,040 | 30,696 | 9,513 | 2.4% | 1.7% |
| `jpegxl_module` | 99,320 | 30,608 | 8,540 | 8.6% | 1.5% |
| `crash_and_burn` | 97,504 | 30,608 | 8,580 | 8.8% | 1.5% |

**Decomposed pipeline total (5 modules, both inference stages):** 5,415,240 raw -> **60,402** stripped+brotli
**Monolith:** 2,487,664 raw -> **18,260** stripped+brotli

So the whole monolith is 3.2% of one 480s pass at 9600 bit/s (~15 s of uplink), and swapping the single largest stage costs ~12 s.

### Per-module share of the monolith (the published metric)

| Module | Raw share | Stripped+Brotli share |
|---|---:|---:|
| `demosaic` | 17.2% | 51.5% |
| `tile` | 13.8% | 60.5% |
| `tflite_classification` | 84.8% | 77.1% |
| `tflite_binary_segmentation` | 84.7% | 76.7% |
| `jpegxl_encode` | 17.1% | 64.9% |

**Range: 14-85% raw becomes 51-77% stripped+compressed.**

Sum of the decomposed modules is 3.3x the monolith compressed (60,402 vs 18,260 B), because each module re-includes its own copy of the shared utility code.
