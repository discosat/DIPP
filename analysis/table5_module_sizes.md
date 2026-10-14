## Table V (corrected) -- module sizes for uplink

| Module | Raw | Stripped | +Brotli | Brotli/raw | Uplink pass |
|---|---:|---:|---:|---:|---:|
| `demosaic` | 414,432 | 34,792 | 10,148 | 2.4% | 1.8% |
| `tile` | 346,624 | 34,792 | 11,435 | 3.3% | 2.0% |
| `tflite_classification` | 2,313,416 | 47,088 | 15,210 | 0.7% | 2.6% |
| `tflite_binary_segmentation` | 2,315,320 | 47,088 | 15,323 | 0.7% | 2.7% |
| `jpeg2000_module` | 111,024 | 30,608 | 9,921 | 8.9% | 1.7% |
| `single_module` | 2,388,936 | 55,280 | 18,202 | 0.8% | 3.2% |
| `resize` | 394,552 | 30,696 | 9,767 | 2.5% | 1.7% |
| `jpegxl_encode` | 429,504 | 38,888 | 12,221 | 2.8% | 2.1% |
| `jpegxl_module` | 102,304 | 30,608 | 8,941 | 8.7% | 1.6% |
| `crash_and_burn` | 80,336 | 34,704 | 8,601 | 10.7% | 1.5% |

**Decomposed pipeline total (5 modules, both inference stages):** 5,500,816 raw -> **62,037** stripped+brotli
**Monolith:** 2,388,936 raw -> **18,202** stripped+brotli

So the whole monolith is 3.2% of one 480s pass at 9600 bit/s (~15 s of uplink), and swapping the single largest stage costs ~13 s.

### Per-module share of the monolith (the published metric)

| Module | Raw share | Stripped+Brotli share |
|---|---:|---:|
| `demosaic` | 17.3% | 55.8% |
| `tile` | 14.5% | 62.8% |
| `tflite_classification` | 96.8% | 83.6% |
| `tflite_binary_segmentation` | 96.9% | 84.2% |
| `jpeg2000_module` | 4.6% | 54.5% |

**Range: 5-97% raw becomes 55-84% stripped+compressed.**

Sum of the decomposed modules is 3.4x the monolith compressed (62,037 vs 18,202 B), because each module re-includes its own copy of the shared utility code.
