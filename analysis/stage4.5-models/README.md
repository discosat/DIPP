# Stage 4.5 — Do the models work?

They return an answer. The answer is not related to the image.

Reproduce:

    ../.venv-tflite/bin/python analysis/model_isolation.py          # models vs EuroSAT
    ../.venv-tflite/bin/python analysis/preprocess_replica.py --sweep
    ./tools/campaign.py experiments/matrices/model-sensitivity.yaml # 5 scenes on hardware

## 1. The models are healthy on the data they were trained for

EuroSAT `testing/` split, 5,400 images, RGB, bilinear 224×224, raw 0–255 —
exactly what training did. **This is a genuine holdout**: the training script
builds `val_ds` from `TRAIN_DIR`, not `TEST_DIR`, so no held-out number has
ever existed for these models.

| model | effort | top-1 | SeaLake | Residential | Forest |
|---|---|---:|---:|---:|---:|
| `custom_conv_model_landcover` | **low** | **75.7%** | 85.0% | 94.5% | 92.8% |
| `mobilenetv2_05_landcover` | medium | 48.9% | 10.7% | 95.7% | 89.3% |
| `mobilenetv2_14_landcover` | **high** | 65.9% | 94.5% | 90.3% | 24.5% |

## 2. ⚠️ The effort ladder is inverted

| effort | latency hint | energy hint | accuracy |
|---|---:|---:|---:|
| low | 235 ms | 465 µWh | **75.7%** |
| medium | 598 ms (2.5×) | 1200 µWh (2.6×) | 48.9% |
| high | 1560 ms (6.6×) | 3180 µWh (6.8×) | 65.9% |

**Low→high costs 6.6× the latency and 6.8× the energy for 9.8 points *less*
accuracy.** This is the direct answer to the reviewer's question about the
value of best-effort, and as it stands the value is negative: the scheduler
spends energy to make classification worse. No adaptive-effort argument
survives this, so it has to be fixed before Stage 5, not explained away.

## 3. On real imagery the pipeline tracks weather, not geography

Sentinel-2-derived scenes on hardware, pipeline 2, 99 tiles each:

| scene | dominant classes |
|---|---|
| amager, clear (**city**) | PermanentCrop 60, HerbaceousVeg 28, Residential 5 |
| koge, clear (**half open sea**) | PermanentCrop 55, Forest 33, Highway 7 |
| amager, light cloud | PermanentCrop 37, Industrial 26, Highway 14, River 11 |
| koge, light cloud | PermanentCrop 36, Forest 24, Industrial 20, Highway 10 |

Total-variation distance between histograms:

| comparison | distance |
|---|---:|
| **geography** — city vs open sea, same weather | **0.38** |
| geography — city vs open sea, both cloudy | 0.21 |
| **weather** — same city, clear vs cloudy | **0.56** |
| weather — same coast, clear vs cloudy | 0.31 |

**Cloud cover moves the output more than the difference between a city and the
open Baltic.** That is the signature of a classifier keying on global
radiometry rather than content. And **class 9 (SeaLake) is predicted 0 times
out of 99 on both sea scenes**, in every configuration tested.

So the answer to "does the output change when the input changes" is: yes, but
it changes with illumination, not with what is in the picture. Retention,
downlink volume and the effort mix are all computed from these decisions.

The host replica reproduces the device exactly — device `amager-no-clouds`
gives `6:60 2:28 7:5`, replica gives PermanentCrop 59% / HerbaceousVeg 28% /
Residential 6% — so host conclusions transfer.

## 4. The cause is radiometry, not scale

Pushing labelled EuroSAT through each suspected mismatch, and measuring:

| EuroSAT variant | top-1 |
|---|---:|
| as-is | **74.3%** |
| matched to the device's channel statistics | **23.3%** |
| downsampled 224→64→224 (EuroSAT's native detail) | 71.8% |

**Colour costs 51 points; scale costs 2.5.** The scale hypothesis is dead —
`resize_module.cpp` is not the answer. The device's tiles are green-dominant
and blue-suppressed (mean RGB `[70, 82, 60]`, σ `[50, 37, 31]`) against
EuroSAT's `[63, 63, 69]`, σ `[52, 43, 40]`. For water, blue is the single most
discriminative channel and the device inverts it: EuroSAT SeaLake averages
`[49, 74, 90]`, device sea tiles `[62, 82, 56]`.

## 5. But no simple colour correction recovers it

All measured, none produces a single SeaLake prediction:

| treatment | separation | SeaLake on sea |
|---|---:|---:|
| device today (joint min–max stretch) | 0.38 | 0% |
| per-channel min–max stretch (white balance) | 0.39 | 0% |
| per-channel 2–98 percentile stretch | 0.24 | 0% |
| per-frame affine calibration to EuroSAT statistics | 0.25 | 0% |
| per-tile affine calibration to EuroSAT statistics | 0.07 | 0% |

Per-tile calibration is actively harmful — it normalises away the very contrast
that separates water from land. **The gap is not a global transform, so it
cannot be fixed in the demosaic module alone.**

## 6. ⚠️ The channel-order bug is real but currently self-cancelling

`demosaic_module.cpp` calls `cv::COLOR_BayerRG2BGR` and labels the output BGR;
the models want RGB. But the Bayer code does not match this sensor's pattern,
so it swaps R and B — which undoes the BGR/RGB error. Measured on a known
vegetation region the device array reads G=102.6 > R=89.1 > B=61.3, which is
correct for vegetation; read as BGR it would not be.

**So the device already feeds approximately-correct RGB, by accident.** Fixing
either half alone drops the model to **16.5%** (measured, BGR column of the
isolation test) — barely above the 10% chance floor. Anyone tidying this code
without reading this file will silently destroy the results.

## 7. Two memory-safety defects, to fix regardless

- `tflite_classification.cpp`: `memcpy(input_tensor, input_image_data,
  expected_bytes)` never checks the image is that long — out-of-bounds read.
- `tflite_binary_segmentation.cpp`: `memcpy(input_tensor, input_image_data,
  size)` never checks `size` fits the tensor — heap overflow.
- Neither null-checks `FlatBufferModel::BuildFromFile` before
  `InterpreterBuilder builder(*model, resolver)`.

Today both sizes happen to be 150,528. Any config change makes one of them a
silent corruption.

---

## 8. The cloud-segmentation ladder is inverted too

38-Cloud, 200 seeded patches, cloud-class IoU. Ground truth is 48.6% cloud, so
**predicting "all cloud" scores IoU 0.486** — that is the floor to beat.

| effort | model | size | latency | energy | best IoU |
|---|---|---:|---:|---:|---:|
| low | `custom_tiny_conv_unet` | 75 KB | 120 ms | 220 µWh | **0.594** |
| high | `mobilenetv2_14_unet` | 5.4 MB | 1472 ms (12.3×) | 3540 µWh (16×) | 0.577 |

**12.3× the latency and 16× the energy for slightly *worse* IoU**, and both are
barely above the constant-prediction floor. Under `minmax` — the mapping the
device actually applies — the low model collapses to **0.298**, worse than
predicting every pixel cloudy.

So both model families have the same defect: effort buys nothing. This is a
prerequisite for DIPP rather than a property of it — an adaptive scheduler that
chooses between effort levels needs those levels to differ in quality, or there
is nothing to choose between.

---

## 9. In-domain baseline: hand-labelled tiles from real frames

EuroSAT accuracy says whether a model works on EuroSAT. This says whether it
works on what the satellite actually produces. 64 tiles hand-labelled off
`grid_*.png` (`device_labels.json`), only unambiguous ones — open water with no
coastline, dense urban with no fields — so failure here is not failure on a
hard case.

| model | effort | overall | SeaLake (43 tiles) | Residential (21) |
|---|---|---:|---:|---:|
| `custom_conv_model_landcover` | low | 4.7% | **0.0%** | 14.3% |
| `mobilenetv2_05_landcover` | medium | 25.0% | **0.0%** | 76.2% |
| `mobilenetv2_14_landcover` | high | 28.1% | **0.0%** | 85.7% |

**Not one of 43 open-water tiles is classified SeaLake by any model.** The
low-effort model is below chance overall. Note the ordering here is the
*reverse* of the EuroSAT ordering — the model that is best on EuroSAT is worst
in domain — which is what over-fitting to a clean, well-exposed dataset looks
like.

## 10. Why: the frame-wide stretch destroys per-tile contrast

Measured over all 495 tiles of the five real frames
(`device_stats.json`) against EuroSAT:

| | R | G | B |
|---|---:|---:|---:|
| device tile within-tile σ | 27.4 | 23.4 | 24.0 |
| EuroSAT within-image σ | 52.0 | 43.2 | 40.3 |
| device tile mean, spread across tiles | 91.5 ± 52.8 | 102.6 ± 43.9 | 89.9 ± 45.3 |

`cv::normalize(NORM_MINMAX)` sets the range from the *whole frame's* extremes,
so any one 224×224 tile occupies a fraction of it: device tiles carry roughly
**half the contrast** of EuroSAT patches, with brightness varying from mean 1
to mean 240 depending on cloud cover. That is the domain gap, and it is why the
`koge-coast-full-clouds` run put **all 99 tiles in a single class**.

## 11. Ladder design

Tile size stays at 224×224 and the tiler is not touched. Changing it would only
produce more tiles, leaving total inference work unchanged — the ground area
per inference has to stay constant for a cost comparison to mean anything.
Effort scales by **downsampling the tile that the module is handed**, so a
low-effort model can take a 64×64 or 112×112 input covering the same ground.

This also removes a memory-safety defect for free: the module currently does
`memcpy(input_tensor, input_image_data, expected_bytes)` with no size check.
Resizing the tile to whatever the interpreter reports as its input shape is
both the feature and the fix.

Targets: quality strictly increasing with effort, with gaps wide enough that
spending 6.6× the energy is defensible, measured on the in-domain set above —
not on EuroSAT alone.

---

## 12. Root cause: no photometric augmentation, and an uncalibrated pipeline

The original training used `RandomFlip`, `RandomRotation`, `RandomZoom`,
`RandomTranslation` — **geometric augmentation only, no photometric
augmentation at all**. So the models learned absolute band values rather than
spectral structure. Subtracting a per-channel dark-object offset from EuroSAT,
roughly the difference between a top-of-atmosphere and a surface-reflectance
product, is enough to destroy them:

| class | EuroSAT as-is | after dark-object subtraction |
|---|---:|---:|
| SeaLake | 87.3% | **0.0%** |
| Residential | 95.3% | **1.3%** |
| Forest | 94.7% | **0.0%** |
| AnnualCrop | 82.7% | 3.3% |

And our frames *are* on the other side of that difference. Measured over the
hand-labelled tiles:

| | R | G | B | texture σ |
|---|---:|---:|---:|---:|
| device SeaLake | 7.7 | 37.6 | 32.9 | 4.1 |
| EuroSAT SeaLake | 50.4 | 76.0 | 91.3 | 3.2 |
| EuroSAT Forest | 39.1 | 63.9 | 76.2 | 3.7 |

EuroSAT's SeaLake and Forest have almost identical colour *ratios* (B/G = 1.20
vs 1.19) and identical texture, so within EuroSAT they are separated almost
entirely by absolute brightness. Our water sits far below both, and lands on
Forest.

## 13. The fix is radiometric calibration, not model invariance

Making the model invariant to brightness is self-defeating: brightness is the
only cue separating SeaLake from Forest. Retraining with aggressive photometric
augmentation confirmed this — it took Residential from 14.3% to 100% and left
SeaLake at 0%, with 41 of 43 water tiles going to Forest.

Calibrating the *input* instead works. Fitting a per-band affine so the pooled
pixel statistics of all 495 device tiles match those of EuroSAT — **no class
labels used** — gives `a = [0.825, 0.658, 0.552]`, `b = [14.3, 36.5, 55.8]`.
The offsets rise toward blue, which is the signature of Rayleigh path radiance,
and an independent fit from the two class means gives the same shape.

| model | uncalibrated | calibrated |
|---|---:|---:|
| `custom_conv_model_landcover` (shipped, low) | 4.7% | **84.4%** (SeaLake 86.0%) |
| `mobilenetv2_14_landcover` (shipped, high) | 28.1% | 45.3% |
| `landcover_low` (retrained, 64×64) | 32.8% | 76.6% (Residential 100%) |

**The shipped low-effort model goes from 4.7% to 84.4% with six numbers.** So
the models were never the main defect; `cv::normalize(NORM_MINMAX)` was, and
replacing it with a fixed calibration is the change that matters.

⚠️ The affine is fitted on pooled statistics of the same five frames it is
evaluated on. It uses no labels, and the statistics are dominated by content
unrelated to the 64 labelled tiles, but a production version should derive the
constants from a separate calibration scene or from an atmospheric model rather
than from the evaluation frames.

## 14. Where the ladder stands

Retrained `landcover_low` is a 64×64 model (111 KB) that survives radiometric
shift where the shipped one does not:

| | clean | DOS | device stats | ×0.5 | brightened |
|---|---:|---:|---:|---:|---:|
| shipped `custom_conv` (224×224) | 75.3% | 10.5% | 12.8% | 10.1% | 13.6% |
| retrained `landcover_low` (64×64) | 71.9% | 65.0% | 45.6% | 61.2% | 69.2% |

3.4 points of clean accuracy for robustness across the board, at 1/12 the input
pixels. The ladder itself is still not monotone and is the remaining work.
