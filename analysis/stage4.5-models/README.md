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

---

# Retraining the ladder — quantisation is the binding constraint

The first retrained ladder was monotone but badly shaped: 79.8 / 96.9 / 98.3
validation. The top two rungs are 1.4 points apart, so nothing would ever pay
for high. Fixing that meant measuring the cost/quality curve rather than
assuming it, which turned up a larger problem.

## Post-training quantisation, not training, is what limits the top rung

Float and quantised top-1 on the EuroSAT `testing/` split diverge only for the
largest model:

| rung | float | uint8 tflite | loss |
|---|---:|---:|---:|
| custom CNN @64 | 78.7% | 80.5% | +1.8 |
| MobileNetV2 α=0.35 @128 | 96.5% | 96.0% | −0.5 |
| **MobileNetV2 α=1.4 @224** | **98.2%** | **78.4%** | **−19.8** |

α=1.4 trains to 98.2% and then loses twenty points on export. Calibration is
not the cause — 300/1000/2000 representative samples, with and without the
photometric augmentation, all land within a point of each other (79.8, 78.6,
79.4, 78.4). Wide MobileNetV2 depthwise layers have activation ranges that
per-tensor int8 cannot represent, and no amount of calibration data changes
that. `tensorflow_model_optimization` (QAT, the real fix) is absent from the
container and has poor Keras 3 support, so the practical answer is to choose
backbones that survive export.

**The model that runs on the satellite is the quantised one, so every rung is
selected on quantised accuracy.** Selecting on float accuracy would have put a
78% model at the top of the ladder and called it 98%.

## The candidate curve

Quantised top-1 (EuroSAT testing, 800-image seeded sample), host-CPU latency as
a *ranking* proxy only:

| candidate | res | size | host ms | EuroSAT | device tiles |
|---|---:|---:|---:|---:|---:|
| custom CNN @64 | 64 | 111 KB | 1.2 | 80.5% | 92.2% |
| custom CNN @96 | 96 | 111 KB | 1.3 | 86.2% | 76.6% |
| custom CNN @112 | 112 | 111 KB | 1.8 | 88.6% | 79.7% |
| custom CNN @128 | 128 | 111 KB | 2.4 | 88.8% | 76.6% |
| MobileNetV2 α=0.35 @96 | 96 | 636 KB | 0.7 | 96.2% | 76.6% |
| MobileNetV2 α=0.35 @128 | 128 | 636 KB | 1.3 | 96.0% | 84.4% |
| MobileNetV2 α=0.35 @224 | 224 | 636 KB | 3.9 | 90.8% | 98.4% |
| MobileNetV2 α=1.4 @224 | 224 | 5.0 MB | 20.7 | 78.4% | 95.3% |

Two things this table says that the accuracy column alone does not.

**Host-CPU latency cannot pick the rungs.** It ranks α=0.35 @96 as
simultaneously the fastest candidate and among the most accurate, which would
dominate every other option. That is an artifact of XNNPACK, not a property of
the board: the i.MX8MP runs these through the VX delegate on the NPU, whose
cost structure is nothing like it. Latency and energy have to be measured on
the board, which is what `tools/bench_models.py` does.

**The custom CNN is the wrong shape for a cheap rung.** It runs four dense
convolutions at full resolution before pooling, so it costs *more* than
MobileNetV2 α=0.35 while scoring eight points lower. It is small on disk and
expensive to run — the opposite of what a low rung needs.

---

# The ladder, measured on all three axes

Accuracy is quantised top-1 on the EuroSAT `testing` split, measured under
TFLite **2.9.1** — the version the board runs. Latency is wall time across all
99 tiles of one frame, taken from the module's own per-tile timestamps on the
board. Energy is the Otii reading for the whole run.

| model | res | ms / 99 tiles | quantised | float | run Wh |
|---|---:|---:|---:|---:|---:|
| **`t9_cnn112`** | 112 | **151.7** | **84.6%** | 84.6% | 0.04845 |
| `custom_conv` (original low) | 224 | 180.7 | 75.7% | — | 0.04863 |
| `t9_cnn064` | 64 | 254.6 | 77.8% | 79.0% | 0.04828 |
| **`t9_a035r096`** | 96 | **396.5** | **96.0%** | 96.0% | 0.04850 |
| `t9_a035r224` | 224 | 484.8 | 96.1% | 98.2% | 0.04940 |
| `mobilenetv2_05` (original medium) | 224 | 488.3 | 48.9% | — | 0.04925 |
| `t9_a035r128` | 128 | 514.7 | 82.1% | 93.0% | 0.04983 |
| `t9_a100r224` | 224 | 912.5 | 89.2% | 98.2% | 0.05026 |
| `mobilenetv2_14` (original high) | 224 | 1118.4 | 65.9% | — | 0.04966 |

**Every one of the paper's three models is Pareto-dominated by a retrained one.**
The published high-effort model costs 1118 ms for 65.9%; `a035r096` costs 397 ms
for 96.0% — 2.8× faster and thirty points better.

## Only two rungs currently earn their place

The Pareto front is `cnn112` (152 ms, 84.6%) and `a035r096` (397 ms, 96.0%).
Everything else is dominated, and two of those are worth naming because they
look like obvious rungs until they are measured:

- `a035r128` is **slower and less accurate** than `a035r096` — 515 ms for 82.1%
  against 397 ms for 96.0%. Its float accuracy is 93.0%; quantisation takes
  eleven points off this one and none off its neighbours.
- `cnn064` is **slower** than `cnn112` despite a quarter the pixels, and less
  accurate. Nothing about the architecture predicts that.

Neither is visible from a host-CPU benchmark or a float validation number.

## Quantisation, not capacity, is what caps the ladder

Loss on export, from float validation to quantised top-1:

| width | float | quantised | loss |
|---|---:|---:|---:|
| α=0.35 @96 | 96.0% | 96.0% | 0.0 |
| α=0.35 @224 | 98.2% | 96.1% | 2.1 |
| α=1.0 @224 | 98.2% | 89.2% | 9.0 |
| α=1.4 @224 | 98.2% | 78.4% | 19.8 |

Every model trains to ~98%. What separates them is how much survives
per-tensor int8, and that degrades monotonically with width. **The achievable
ceiling on this board is therefore about 96%, not 98%**, and a third rung
meaningfully above `a035r096` needs quantisation-aware training rather than a
bigger backbone. That is a real finding for the paper: on this hardware the
effort ladder is bounded by the export path, not by how much compute the
scheduler is willing to spend.

## Latency is overhead-bound at the cheap end

`cnn112` runs 99 tiles in 151.7 ms, i.e. ~1.5 ms per invocation, and `cnn064`
is *slower* at the same tile count. At that end the per-invocation cost of
crossing into the delegate dominates the arithmetic, so a cheaper model buys
nothing. The low rung cannot go below roughly 150 ms per frame however small it
gets — which also means the honest way to make low effort cheaper is to run
fewer tiles, not a smaller network.

## Cloud segmentation

Trained and converted, IoU on a held-out 38-Cloud split:

| model | res | width/depth | val IoU |
|---|---:|---|---:|
| `cl_x96w4` | 96 | 4/2 | 0.741 |
| `cl_tiny` | 96 | 6/3 | 0.834 |
| `cl_low` | 128 | 8/3 | 0.846 |
| `cl_high` | 224 | 24/4 | 0.865 |

Against the deployed models' 0.594 and 0.577, every rung is a large
improvement, and `cl_x96w4` to `cl_high` spans 12 IoU points where the first
attempt spanned three. **These have not yet been benchmarked on the board** —
`bench_models.py` drives slot 3 (classification) and measuring the segmentation
rungs needs the same treatment for slots 6 and 7.
