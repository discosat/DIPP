# Stage 4.5 — Model correctness · CLOSED 2026-09-15

**Paper-facing summary.** Three stages, three effort levels each (two for
JPEG XL), every level with a quality, a latency and an energy figure; the
pipeline composed end to end on labelled Sentinel-2 frames through the real
demosaic; and the value of stepping up an effort level measured in two views.
Everything below this summary is the chronological record — how each number
was arrived at, including the wrong ones on the way. Selection was done on
`validation`; `test` was scored once, at the end (this section).

**Rendering** frozen: uniform gain 0.050, zero offset (`module_1.yaml`). Cost
figures: PicoCoreMX8MP, NPU via VX, warm, per 99-tile frame of
`cs12_validation_00`, energy idle-corrected with a floor read before (and
after) every run and the upload term removed (§ *Energy, calibrated*).

### Classification — slots 3 / 4 / 5 (`landcover_ms_{low,medop,high}`, `class_index 9` = water)

| rung | model | EuroSAT-MS test top-1 | CS12 water recall / precision, val → **test** | EXEC ms | µWh |
|---|---|---:|---|---:|---:|
| low | custom_conv @224, QAT | **75.13 %** | 78 / 71 → **70 / 62 %** | 498 | **219** |
| med | MobileNetV2 α=0.5, stopped at val 0.90, QAT | **92.09 %** | 96 / 83 → **85 / 62 %** | 851 | **394** |
| high | MobileNetV2 α=1.4, QAT | **98.52 %** | 93 / 79 → **85 / 82 %** | 1,820 | **849** |

5,400-image holdout, σ ≈ 0.4 pp. CS12 gate: tiles ≥60 % water (WorldCover),
≤10 % cloud (expert mask); 465 validation / 131 test water tiles. The medium
rung is an operating point (α=0.5 to exhaustion = 98.57 %).

### Segmentation — slots 6 / 19 / 7 (`cs12g050[q]_*`, threshold 0.5 = "not majority cloud")

| rung | model | IoU val → **test** | precision / recall (test) | FP on clear (test) | EXEC ms | µWh |
|---|---|---:|---|---:|---:|---:|
| low | plain U-Net w8d3 @96, PTQ | 0.639 → **0.607** | 0.887 / 0.657 | 3.0 % | 566 | **163** |
| med | MobileNetV2 α=0.35 @160, selective QAT | 0.789 → **0.775** | 0.903 / 0.845 | 1.5 % | 1,431 | **496** |
| high | MobileNetV2 α=1.4 @224, selective QAT | 0.811 → **0.807** | 0.910 / 0.877 | 0.9 % | 4,158 | **2,486** |

535 validation / 975 test expert-labelled scenes. Steps above noise (paired
scene bootstrap on validation: +0.150, +0.021 [+0.009, +0.034]).

### Encoder — JPEG 2000 slots 16 / 17 / 18 (flight); JPEG XL slots 11 / 12 (comparison), bytes frozen at 3 kB/tile

| rung | config | PSNR, one 512 B frame lost | clean PSNR | ms/tile | µWh/frame |
|---|---|---:|---:|---:|---:|
| J2K L1 | 1 quality layer, ratio 49.22 | **32.51 dB** | 41.39 | 34.0 (48.3) | **611** (738) |
| J2K L2 | 2 layers, 98.2 | **36.04** | 41.17 | 45.6 (61.8) | **741** (884) |
| J2K L3 | 3 layers, 147.56 | **37.88** | 40.93 | 59.0 (75.9) | **894** (1,026) |
| JXL e3 | distance 2.358 | decodes 0–10 % | 40.49 | 25.6 (27.9) | **607** (573) |
| JXL e5 | distance 2.884 | decodes 0–10 % | 40.93 | 189.5 (204.9) | **2,770** (2,910) |

Costs on the matched frame (clear water, the encoder's flight content; random
CloudSEN12+ frame in parentheses — J2K is 13–17 % cheaper on smooth content,
JXL's time is content-independent but its bytes fall to 536 / 668 B there).

300 retained corpus tiles, board codec versions (openjpeg 2.4.0, libjxl
0.10.2). J2K layers are a robustness ladder (one-frame-lost PSNR peaks at
three layers at every budget); JXL's effort curve at fixed bytes is a step
function, {1–4} < {5–9}, hence two levels.

### Fixed stages and pipelines

| | latency | µWh/frame |
|---|---:|---:|
| demosaic + tile (slots 1, 2), one-pass rendering (2026-09-17) | **241 + 99 ms** | **57 ± 20** (was 690 + 143 ms, 216 µWh) |
| I/O per 15 MB uploaded | — | 304 |
| pipeline 2 · 6 · 3 on the **Earth-average matched frame** (23 / 48 / 28), one-pass modules, n=20 | **2,140 · 3,397 · 6,482 ms** | **519 · 968 · 2,744** |
| pipeline 2 · 6 · 3 on the keep-all matched frame (99 reach the encoder), one-pass modules, n=20 | 4,742 · 7,124 · 11,739 ms | 952 · 1,571 · 3,952 |
| the same before the demosaic fix | 5,213 · 7,661 · 12,484 | 1,099 · 1,747 · 4,172 |
| pipeline 2 · 6 · 3 on a random frame, old modules, n=50 ×2 | 1,655 · 2,707 · 4,204 | 570–637 · 830–884 · 1,619–1,625 |

**Earth-average matched frame — the experiments' mode: span 5.3× energy,
3.0× latency**, retention 71 / 70 / 69 at classification and 23 / 23 / 22 at
the encoder for low / medium / high (targets 71 and 23; the residual is NPU
vs host arithmetic at the boundary). Keep-all frame: 4.2× / 2.5×, the
composition from the stage tables reproducing latency to <1 % and energy to
8–10 %. (Random frame, old modules: 2.7× / 2.5×.) Pipeline 1 = adaptive J2K (27 operating
points), 5 = adaptive JXL (18), 4 = monolith.

### Retention and outcome (CloudSEN12+, exact module chain), val → **test**

| classification | keeps | reaches encoder | of which water & clear | delivered / 99-tile frame |
|---|---:|---:|---:|---:|
| low | 4.3 → **4.1 %** | 4.0 → **3.4 %** | 72 → **56 %** | 2.87 → **1.9** |
| med | 19.6 → **20.4 %** | 9.3 → **9.5 %** | 42 → **31 %** | 3.84 → **2.9** |
| high | 14.9 → **15.3 %** | 9.2 → **8.0 %** | 42 → **37 %** | 3.79 → **2.9** |

(seg and enc at the same rung; 2,140 validation / 3,900 test tiles; the
corpora hold 4.2 / 3.7 % water-and-clear tiles.) **Classification-high is
dominated on mission outcome on both splits** — it delivers what the medium
rung delivers at 1.5–1.6× the frame energy — in the normal view and in the
retention-matched view (§ *The value of an effort level*). Swapping
segmentation ahead of classification changes cost only (+163 / +496 / +2,486
µWh per frame). On hardware, all five pipelines run end to end on CloudSEN12+
frames and score against the labels (§ *Phase 4*).

### Decisions

- **Value per stage, on each stage's own test set** (the rungs were not
  trained on the joint task; the joint outcome is the viability gate only);
  **experiments run retention-matched** on the `cs12_test_matchedkeep_*` frames,
  where every stage processes all 99 tiles at every effort. Encoder cost
  hints from the matched frame.
- Rendering gain 0.050; target class water; medium classification is an
  operating point; selective QAT for MobileNet segmenters; J2K flies, JXL has
  two levels; bytes frozen per codec; device frames retired, every board run
  on CloudSEN12+ frames.
- Flight-config cost hints written from the measurement files
  (`tools/update_energy_hints.py`); the demosaic/tile latency hints (373 /
  143 ms) predate the CloudSEN12+ frames and should be re-hinted the same way.

### Reproduce

    tools/phase1_cls_{train,export,board}.sh            # classification
    tools/phase2_seg_{train,export,board}.sh            # segmentation
    analysis/encoder_corpus_eval.py --report            # encoder quality
    tools/energy_calibration2.sh; tools/phase3_enc_energy.sh; analysis/energy_cal_summary.py [--enc]
    tools/phase4_e2e.py --n 50 --idle 60; analysis/e2e_energy.py
    analysis/pipeline_retention.py [--split test]; analysis/value_of_effort.py
    analysis/cs12_validate.py --split test; analysis/cs12_classifier_validate.py --split test

Files: `stage_energy.json`, `encoder_energy.json`, `e2e_static50*.json`,
`pipeline_retention{,_test,_swapped}.json`, `value_of_effort.json/.png`,
`cs12_test_g050.json`, `classifier_test_ms.json`, `e2e_runs_scored.json`.

---

# Chronological record

## Stage 4.5 — Do the models work? (2026-08-31, the starting point)

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

---

# The encoder ladder

## A crash that was never in the codec

Every JPEG 2000 config failed with `MODULE_EXIT_CRASH`, error **600201**, at
every quality-layer count. Two commits went into the encoder's memory sink
chasing it. Both were wrong, and the error code says so in plain sight:

    get_error_as_uint32:  code * 1000 + err_current_pipeline * 100 + err_current_module
    pipeline_executor.c:  err_current_module = i + 1;

So 600201 is pipeline 2, module index **0** — `libdemosaic`. The JPEG 2000
module never ran. The real cause was the board:

| | |
|---|---|
| leaked SysV segments | **61** |
| held | **1,590,725,121 B (1.59 GB of 1.95 GB)** |
| available memory | **46 MB** |

`image_batch_cleanup` detaches with `shmdt` but never `shmctl(IPC_RMID)` in the
`STORAGE_MEM` path — the SHM→MMAP conversion path *does* RMID, which is why this
only bites in MEM mode, the mode every benchmark uses. Segments accumulated
across sweeps until nothing could allocate, and `libdemosaic` — the first module
to run and the one carrying the full-resolution frame — died first. Every
config then failed identically regardless of its parameters, which is exactly
what a codec bug looks like.

**Three lessons, all of them repeats of this revision's central theme.** The
failure was uniform across configs, which should have pointed at the
environment rather than the parameter under test. The error code identified the
wrong module for three board round-trips because nobody decoded it. And the
host reproducer that settled it — all 16 configs, 99 tiles each, clean under
AddressSanitizer against openjpeg 2.4 — took ten minutes and should have come
first.

Reclaiming is now unconditional: `tools/shmclean.c` (the board's BusyBox has no
`ipcrm`/`ipcs`) runs before every config from `bench_models.py`, which **aborts
rather than measures** if it cannot confirm a clean machine. Fixing the leak
itself is A8/Stage 5; the daemon is deliberately left untouched here so these
numbers stay comparable with the ones already taken.

⚠️ **The earlier J2K measurements were valid after all.** 42.6 ms/tile at one
layer reproduces post-reclaim against 42.0 ms/tile before, so the figures
retracted as "measured with a buffer overrun present" are reinstated.

## resolutions is a dominated knob

At a fixed ~3000 B/tile, decomposition count moves quality enormously — and
moves cost the wrong way for a ladder:

| resolutions | PSNR | host ms | device ms/tile |
|---|---:|---:|---:|
| 1 | 32.47 dB | 15.2 | **62.4** |
| 2 | 38.26 dB | 12.4 | — |
| 3 | 44.96 dB | 10.2 | — |
| 6 | **47.57 dB** | **11.5** | **42.6** |

Six levels is simultaneously the best quality and the cheapest, on host and on
device, because fewer levels leave larger subbands to code. A knob whose best
setting is also its fastest can never justify spending effort, so `resolutions`
is fixed at 6 and is not a rung.

## Quality layers are the rung, and they buy robustness, not fidelity

Iso-rate ~3000 B/tile, resync markers on, 8 real device tiles, degraded-mode
figures averaged over **24 independent drop patterns**:

| layers | PSNR (full) | host ms | 1 frame lost | 2 frames lost |
|---|---:|---:|---:|---:|
| 1 | **47.32 dB** | 15.6 | 36.8 dB | 35.0 dB |
| 2 | 47.01 dB | 18.0 | 40.1 dB | 36.2 dB |
| 3 | 46.71 dB | 21.5 | **42.1 dB** | 36.2 dB |
| 5 | 45.93 dB | 28.4 | 40.7 dB | **37.7 dB** |

**State this honestly in the paper: this is a robustness ladder, not a fidelity
ladder.** Full-receipt PSNR is mildly *inverted* (−1.4 dB from 1 layer to 5)
because layer signalling costs bytes out of a fixed budget. What the extra
compute buys is degraded-mode fidelity — up to +5.3 dB when a frame is lost —
and that is the property that matters on a CCSDS-framed link, where the radio's
per-frame CRC turns damage into erasures.

Decode *rate* is flat across layer counts (88% at one frame lost, 83% at two).
Survival comes from the resync markers; layers determine only how much quality
survives. The two mechanisms are separate and should not be conflated.

⚠️ **The averaging is not a detail.** A 3 kB tile is ~6 frames and J2K layers
are cumulative refinements, so losing the first frame and losing the last are
different events by tens of dB. A single fixed seed reported 1-layer
degradation as 28.7 dB; averaged over 24 patterns it is 35.0 dB. The
single-seed number measured which frame that seed picked, not the codec.

## How far the J2K ladder can stretch — and why it stops

The paper's encoder ladder spans **1 : 11.4 : 39** in energy (264 / 3,010 /
10,300 µWh at slots 11/12/13). The layer ladder does not come close, and
pushing the layer count does not rescue it:

| layers | rate reached | B/tile | host ms |
|---|---:|---:|---:|
| 1 | 49.03 | 3,039 | 15.9 |
| 5 | 249.58 | 3,003 | 28.8 |
| 12 | **400 (ceiling)** | 4,517 | 53.1 |
| 24 | **400 (ceiling)** | 9,017 | 109.9 |
| 48 | **400 (ceiling)** | 16,211 | 256.9 |

⚠️ **The last three rows are not measurements of a rung.** The bisection
saturated at its rate ceiling and never reached the 3,000 B budget, so those
tiles are 1.5–5.4× over budget. They are not iso-rate and must not be read as
"more layers cost more time" — they cost more time *and* send five times the
bytes. The honest statement is the reason they saturated: each quality layer
carries its own signalling, and past roughly five layers that overhead alone
exceeds a 3 kB tile. **The layer count is bounded by the byte budget**, so the
usable range really is 1–5.

**The mechanism is why, and it generalises.** JPEG 2000 encodes every coding
pass and then truncates to the target rate, so its compute is set by image
dimensions, not by the rate or the layer count; layers only add rate-allocation
work on top. There is no "search harder" knob of the kind JPEG XL's effort
provides. That caps the achievable span at roughly **2.4×** on device
(42.2 ms/tile at 1 layer), against the 39× the paper reported.

JPEG XL does span it — effort 1→9 measured on the board is 26.6 → 1300.5
ms/tile, a **48.9×** range that brackets the paper's 39× closely:

| JXL effort | ms/tile | B/tile | run Wh |
|---|---:|---:|---:|
| 1 | 26.6 | 4,167 | 0.0489 |
| 5 | 212.8 | 4,380 | 0.0709 |
| 9 | 1,300.5 | 4,199 | 0.1689 |

**This is a decision for the paper, not something to resolve by tuning.** The
codec that carries the resilience argument is not the codec that can carry a
wide effort ladder, and no choice of J2K parameters changes that.

## The deployed classification ladder, verified

Re-measured directly from the artifacts the flight config points at, on the full
EuroSAT `testing` holdout (5,400 images, RGB, TFLite):

| slot | model | top-1 | separation |
|---|---|---:|---|
| 3 | `custom_conv_model_landcover` | **75.67%** (4086/5400) | — |
| 4 | `qat_a050r224` | **89.98%** (4859/5400) | +14.3 |
| 5 | `qat_a140r224` | **98.17%** (5301/5400) | +8.2 |

Well separated at both steps, and every rung is worth its cost.

⚠️ The top rung is **98.17%, not the 99.0% carried in earlier notes.** Quoting a
figure nobody had recomputed from the deployed file is the same failure mode as
the rest of this revision, so it is recomputed here and the QAT JSONs are noted
as recording the float *target* (0.9191 / 0.8823), not achieved quantised
accuracy — they are not a source for this table.

QAT is what makes the top rung possible: post-training quantisation took α=1.4
to 78.4%, against 98.17% with QAT, on the same architecture.

## The J2K ladder, settled — and why the idle correction decides it

Three rungs, iso-rate ~3000 B/tile, resync on, **n=4** on the board:

| slot | layers | ms/tile | latency_cost | work energy | PSNR full | 1 frame lost |
|---|---:|---:|---:|---:|---:|---:|
| 16 low | 1 | 42.1 ± 0.0 | 4,172,797 µs | **289 ± 57 µWh** | 47.32 dB | 36.8 dB |
| 17 medium | 3 | 67.7 ± 0.4 | 6,689,717 µs | **552 ± 41 µWh** | 46.71 dB | 42.1 dB |
| 18 high | 5 | 98.6 ± 0.6 | 9,739,221 µs | **917 ± 16 µWh** | 45.93 dB | 40.7 dB |

Ratios — latency **1 : 1.61 : 2.34**, energy **1 : 1.91 : 3.17**. Both monotone,
both clear of the noise floor: L1→L3 is +262 µWh against ±57 (~3.7σ), L3→L5 is
+366 µWh (~8σ).

⚠️ **Read as raw run energy these rungs do NOT separate, and that reading is
wrong.** L=1 alone measured 0.04414 / 0.04380 / 0.04384 / 0.04461 Wh — a spread
of 0.81 mWh, larger than the whole L1→L5 span. Almost all of that variance is
run *length*, not power: runs last 56–81 s and the board idles at **2.7401 W**,
so a few seconds either way swamps a module drawing a few hundred µWh.
Correcting to work-only energy,

    E_work = (E_run - P_idle*T_run) - (E_base - P_idle*T_base)

collapses the within-rung spread to ±16–57 µWh and the ladder separates. The
correction is worth up to **8.6×** (JPEG XL effort 5 reads 21,684 µWh raw
against 2,530 µWh of actual work).

Two independent checks that the corrected numbers are real rather than an
artifact of the correction: two baseline runs doing identical work agree to
102 µWh, and energy tracks latency for both codecs independently — J2K
1 : 1.91 : 3.17 energy against 1 : 1.61 : 2.34 latency, JXL 7.2× against 7.65×.
Encode power is roughly constant, so E ∝ t, which is what the corrected figures
show and the raw ones did not (they implied JPEG XL drew 18× the energy for 2×
the time, which is not physical).

**This is the measurement methodology W1 has to state**, and it is not
optional: every per-module energy figure in the paper is a difference between
runs of unequal length against a board floor larger than the thing being
measured.

---

# Two device defects the models exposed

Slots 4 and 7 had never been run on hardware. Both failed, reproducibly (3x
each), for unrelated reasons, and neither is a bad model.

## 1. Retaining nothing crashed the pipeline

`qat_a050r224` crashed with `MODULE_EXIT_CRASH` (600203) on the NPU **and** on
the CPU, after processing all 99 tiles. Its class histogram for the frame:

| class | 6 | 9 | 0 | 5 | 3 | 2 | 8 | 1 |
|---|---|---|---|---|---|---|---|---|
| tiles | 36 | 15 | 15 | 14 | 14 | 3 | 1 | 1 |

**Class 7 (Residential) never appears**, and `class_index` is 7. So the module
kept nothing — and keeping nothing is what killed it:

    initialize()            sets result->batch_size = 0 and num_images = 0
                            but never sets result->data
    run()                   declares `ImageBatch result_batch;` UNINITIALISED
    append_result_image()   assigns result->data only on its first call
    finalize()              free(result->data)   <- frees stack garbage

One line, `result->data = NULL`, in `initialize()`.

⚠️ **This is a flight bug, not a bench artifact, and it is the interesting kind.**
Retaining nothing is a *normal* outcome — open ocean, full cloud cover, or any
frame lacking the configured class produces it. The neighbouring rung survived
only by accident, because it happened to predict Residential somewhere in the
same frame. It is also undefined behaviour rather than a clean failure, so the
silent-corruption case is as available as the crash.

It bears directly on §IV-C and W4: the fault-tolerance story is about surviving
*module faults*, and this is the pipeline killing itself on a correct result.

## 2. The whole `g` model family is unrunnable on the NPU

`qat_cloud_g_a10_r224` fails VX delegate graph verification — `Failed to verify
graph`, `Node number 106 (Vx Delegate) failed to invoke`, giving `704203`
(MODULE_EXIT_CUSTOM + INFER_ERR). Counting dynamic-shape ops splits the models
exactly:

| family | models | SHAPE/STRIDED_SLICE/PACK | on device |
|---|---|---:|---|
| `g` (MobileNetV2 U-Net) | 4 of 4 | **15** | fails |
| `p` (plain conv U-Net) | 5 of 5 | 0 | runs |
| classification | 2 of 2 | 0 | runs |

`Conv2DTranspose` with batch `None` has to compute its output shape at run time;
the converter emits that as `SHAPE -> STRIDED_SLICE -> PACK`, and the delegate
cannot verify a dynamic-shape graph.

⚠️ **The cause was already written down in `train_cloud.py` and applied to only
one of its two builders.** The comment above the plain-conv builder records the
exact lowering table, and that builder passes `batch_size=1`; the MobileNetV2
builder did not. One missing argument cost four models.

Re-export needs QAT re-run: only the `.tflite` was kept for the cloud models, so
there are no QAT weights to re-convert from, and the float `.weights.npz` is a
pre-QAT artifact. The NPU-safe `p` family tops out at IoU 0.523 against the `g`
family's 0.588, so shipping `p` alone costs ~6.5 points on the rung the
modularity argument leans on.

**Do not treat the `p` results as a fallback that costs nothing** — and do not
read the two families' IoU as interchangeable with the pooled numbers quoted
earlier in this file; these come from `segmentation_isolation.py` on a 400-patch
38-Cloud sample and are a different metric.

## Stage costs, measured in one session

The empty-batch fix makes the middle classification rung runnable, so all three
exist on hardware for the first time. Module span (the module's own per-tile
timestamps) is the stable number; EXEC is the whole forked execution.

| stage | EXEC | module / 99 tiles | quality |
|---|---:|---:|---:|
| classification low `custom_conv` | 273.5 ms | **184.1 ms** | 75.67% |
| classification med `qat_a050r224` | 763.4 ms | **685.7 ms** | 89.98% |
| classification high `qat_a140r224` | 2020.9 ms | **1593.8 ms** | 98.17% |
| segmentation low `p_w08d3_r128` | 4878.9 ms | **4776.0 ms** | IoU 0.461 |
| segmentation high `p_w16d3_r128` | 12954.7 ms | **11683.5 ms** | IoU 0.523 |

Classification spans **1 : 3.72 : 8.66** against 75.7 / 90.0 / 98.2 — the
best-separated ladder of the three stages, and far wider than the encoder's
2.34x.

⚠️ **EXEC is much noisier than the work it measures.** `custom_conv` read 415.8
ms then 273.5 ms for the same configuration, a 52% spread, while its module span
moved 181.8 -> 184.1 ms (1.3%). The difference is fork and model load. The
scheduler's `latency_cost` predicts EXEC, so that variance is the scheduler's
problem, not an artifact to average away -- but a hint set from a single EXEC
sample would be wrong by tens of percent.

⚠️ **The segmentation pair is a poor ladder.** 2.45x the latency buys +0.062
IoU, where the `g`-family pair bought +0.127 for a comparable step. That is the
cost of the NPU-safety workaround stated in latency terms, and it is an argument
for re-exporting `g` rather than shipping `p`.

## The idle floor is not a constant, and the correction survives that

| session | idle | baseline work |
|---|---:|---:|
| J2K ladder | 2.7401 W | 804 µWh |
| J2K repeats | 2.7382 W | 906 µWh |
| stage costs (after reboot, NPU workloads) | **3.2892 W** | 911 µWh |

The floor moved **20%** between sessions -- the first two agree to 0.07%, so
this is a state change, not drift; the board was power-cycled and has since been
running NPU rather than CPU-only workloads. **The corrected work energy is
stable across all three (804 / 906 / 911 µWh) even though the floor beneath it
moved 0.55 W.** That is the correction doing exactly its job, and it is the
argument for W1 stating the method rather than quoting raw deltas.

It also means **energy figures from different sessions are not comparable**, and
every campaign must carry its own idle measurement.

⚠️ **Two rungs measured NEGATIVE work energy** at n=1: -41 µWh (classification
low) and -134 µWh (medium). Nothing is wrong with the board -- 184 ms of module
work inside a 56 s run is simply below the ~100 µWh noise floor, and a negative
difference is what that looks like when read honestly. It is a signal to raise
n, not a number to publish or to clamp to zero. Energy for the cheap rungs is
being re-measured at n=50, with the baseline at the same n (demosaic and tile
run n times too, so an n=1 baseline would charge the module for 49 extra
demosaics).

## A truncating log read, and why it mattered

`bench_models.py` collected executions with `cat console.log` over the serial
console. That works at one batch and silently truncates at fifty:

| | board reported | harness parsed |
|---|---|---|
| 50-batch run | `executions=141 completed=47` | **5 batches** |

So every multi-batch measurement read a fraction of its own data and divided by
the wrong count -- and produced a plausible-looking result rather than an error.
The n=50 energy pass computed from it gave a 7,940 µWh/batch baseline against a
true ~285, and was discarded rather than reported. **Stage 5's campaigns are
100-batch runs and would all have been affected.**

Fixed by reducing on the board -- `grep '^EXEC ' | sed` to `module latency`
pairs, ~30 bytes a line instead of ~400 -- rather than shipping the whole log
over a 9.7 kB/s link. The same run then parsed 47 of 47, at an unchanged EXEC
median (246.7 ms both times), which is what confirms this was a transport limit
and not a board-side difference.

This is the fifth defect of this exact shape in the harness (four in Stage 4).
**A measurement path that cannot fail loudly will fail quietly**, and the serial
console is the common factor in all five.

## Classification cost hints, measured

n=47 completed executions, idle 3.0969 W measured in the same session, energy
differenced against a demosaic+tile baseline at the same batch count:

| slot | rung | latency_cost | energy_cost | top-1 |
|---|---|---:|---:|---:|
| 3 | `custom_conv` | 246,700 µs | **18 µWh** (estimate) | 75.67% |
| 4 | `qat_a050r224` | 746,600 µs | 66 µWh | 89.98% |
| 5 | `qat_a140r224` | 1,691,700 µs | 535 µWh | 98.17% |

Baseline demosaic+tile is 285 µWh/execution.

⚠️ **Slot 3's energy is an estimate and is labelled as such in the config.** Its
measured value was -76 µWh, i.e. below the noise floor even at n=47. The
tempting substitute -- the 100 µWh floor itself -- is actively harmful: it
exceeds slot 4's measured 66 µWh and would tell the scheduler that low effort
costs MORE energy than medium, inverting the ladder inside the single number the
scheduler consults. It is instead the medium rung's measurement scaled by module
time (66 x 186.0/672.4 = 18 µWh), which stays under the floor and keeps the
ordering monotone.

**Marginal power is not constant across rungs** and the hints should not be
derived by assuming it is: 535 µWh over 1580 ms is ~1.22 W marginal, while
66 µWh over 672 ms is ~0.35 W. A bigger model keeps the NPU busier, not merely
busy for longer.

## pixel_threshold and mask_lowpass, validated on the device

Both were host-only until now. `tests/board/run.sh --image` plus the `segjxl`
bench kind puts them on hardware.

**`pixel_threshold` is honoured, and monotonically** (masked channel-values in
one tile, `amager-no-clouds`):

| pixel_threshold | kept | masked |
|---:|---:|---:|
| 0.3 | 149,769 | 759 |
| 0.5 | 150,315 | 213 |
| 0.7 | 150,516 | 12 |

It was hardcoded at 0.5 before this revision, so this is the first evidence the
exposed parameter reaches the model's decision. `mask_lowpass` leaves these
counts identical (150,315 either way), which is correct: it changes pixel
*values*, not the mask.

**The low-pass fill is worth a lot of bytes.** `koge-coast-full-clouds`, JPEG XL
at fixed distance, 99 tiles:

| fill | B/tile | frame |
|---|---:|---:|
| black (`mask_lowpass=0`) | 1,305 | 129,204 B |
| 5x5 low-pass (`mask_lowpass=5`) | **363** | **35,922 B** |

**-72%**, against -5% for JPEG XL on the host. The direction is confirmed and
the mechanism is the expected one -- hard-edged holes are expensive for a
transform codec, which is the same effect that made black fill *enlarge* J2K
output on the host -- but do not quote -72% as the general figure.

⚠️ **Two caveats, and the second is the more important one.**

*The encoder has to be quality-targeted for this to be measurable at all.* The
first attempt used JPEG 2000 at a fixed `compression_ratio`, and every
configuration returned ~3,010 B/tile whatever the fill did, because rate
targeting means content moves quality rather than size. That reads as "the fill
does nothing" and is purely an artifact of the instrument.

*The segmenter is barely masking anything on device.* Across all 99 tiles of a
frame named **full-clouds**, the masked fraction is **0.54%** and nearly
constant (636-858 of 150,528). A cloud segmenter that masks the same thin
slice of every tile of a fully-clouded scene is not tracking content -- this is
the device domain-shift already documented in sections 3, 10 and 13 above,
showing up again. So -72% is measured in a **low-masking regime**, where a few
hundred black pixels happen to dominate a nearly featureless bitstream. It
demonstrates that hard-edged fill is expensive; it does not establish what the
fill saves on a frame that is masked properly.

## Radiometric calibration is active on device, and is not the lever

The module comment blames the legacy `NORM_MINMAX` path for putting "all 99
tiles of the full-cloud frame into a single class", so the obvious suspicion
about the 0.54% masking was that the device was still on that path --
`module_1.yaml` sets only `normalization_value` and never mentions
`radiometric_mode`. It isn't: the parameter defaults to 1, and the deployed
`libdemosaic.so` contains it. Measured both ways on `koge-coast-full-clouds`,
masked channel-values across 99 tiles:

| radiometric_mode | masked | min | median | max |
|---|---:|---:|---:|---:|
| 0 — legacy `NORM_MINMAX` | 0.74% | 675 | 1158 | 1239 |
| 1 — calibrated affine (**default; what runs**) | **0.54%** | 636 | 834 | 858 |

Mode 1 reproduces the default run exactly (636/834/858), which is what confirms
the device has been calibrated all along.

⚠️ **The finding is what both rows share, not their difference.** On a frame
named full-clouds the segmenter masks under 1% either way, and the tile-to-tile
spread is small in both modes -- the output barely responds to content.
Calibration is not the lever here and in fact masks *less*, so the
cloud-detection failure is in the model, not the preprocessing. That closes off
the preprocessing hypothesis rather than confirming it.

**This bears on the `g` vs `p` choice.** The `g` models carry the real IoU
(0.588 against `p`'s 0.523) and have **never executed on this hardware** -- they
fail VX graph verification before producing a tile. So whether a working `g`
model discriminates cloud on device imagery, where `p` demonstrably does not, is
untested. Re-exporting settles the NPU blocker and this question together;
shipping `p` alone leaves a segmentation stage that does not segment.

⚠️ `module_1.yaml` still relies on the module's compiled-in default for
`radiometric_mode`. Flight behaviour this consequential should be stated in the
config, not inherited -- set it explicitly when the segmentation path is next
touched. (It is deliberately left unset here so this file continues to describe
the configuration these measurements were taken under.)

## Can JPEG 2000 buy resilience with compute at a fixed budget?

⚠️ **First, a retraction.** This file previously concluded that quality layers
past ~5 "cannot hold the byte budget" and that the layer count is therefore
bounded by it. That was wrong, and the cause was a search bound in
`j2k_layer_ladder.py`, not the codec: with `cp_disto_alloc` the last layer's
rate is `ratio/layers`, so hitting a byte target needs `ratio ~= 49.8 * layers`
-- 598 at 12 layers, 2390 at 48 -- against a hardcoded `hi=400`. Every
high-layer config was pinned at the ceiling and returned oversized tiles, which
read as "cannot fit" and meant "was never allowed to look there". The bound now
scales with the layer count and warns when a bisection lands near it.

With the bound fixed, L=12 holds 3 kB comfortably. It is still not a rung:

| layers @3 kB | B/tile | PSNR | host ms | 1 frame lost |
|---|---:|---:|---:|---:|
| 1 | 2998 | 46.98 | 16.3 | 37.1 dB |
| 5 | 2997 | 45.54 | 29.1 | **40.5 dB** |
| 12 | 2999 | 41.96 | 43.3 | **21.1 dB** |

Degraded fidelity *collapses* past 5 layers, because layer signalling out of a
fixed budget starves the base layer.

### The dedicated error-resilience machinery does nothing at 3 kB

`-M` cblk styles (RESET 2, TERMALL 4, VSC 8, ERTERM 16, SEGSYM 32) and
precinct/tile partitioning are what JPEG 2000 actually provides for lossy links.
At 3 kB per 224x224x3 tile -- **0.16 bpp** -- they do not fit:

| config @3 kB | B/tile | PSNR | ms | 1 lost |
|---|---:|---:|---:|---:|
| L1 plain | 2998 | 46.98 | 15.4 | 36.5 dB |
| L1 segsym+vsc | 3006 | 46.88 | 16.0 | 36.7 dB |
| L1 termall+all (M=46) | 3041 | 46.41 | 16.9 | 36.3 dB |
| L1 precinct 64 | 3064 | **31.25** | 37.2 | 31.2 dB |
| L3 prec32 tile56 | 43366 | 14.11 | 111.2 | — |

The flags cost ~1.5 ms and buy ~0 dB; partitioning spends the image on packet
headers. **Resilience in JPEG 2000 is bought with BYTES, not compute** -- every
mechanism is a partitioning or signalling cost -- so at a pinned budget compute
has nothing to purchase.

### At a fixed 10 kB budget it does work, but the span stays narrow

Raising the budget is what makes the mechanisms affordable. Every layer count
then holds the target essentially exactly:

| rung @10 kB | B/tile | ms | decode 1/2 lost | PSNR 1/2 lost |
|---|---:|---:|---:|---:|
| L1 | 10,008 | 16.5 | 100% / 97% | 42.9 / 39.6 dB |
| L3 | 10,058 | 23.4 | 100% / 97% | 47.4 / 43.6 dB |
| **L6** | 10,010 | 36.4 | **100% / 100%** | **48.9** / 41.9 dB |
| L12 | 10,009 | 71.3 | 100% / 100% | 45.5 / 43.9 dB |
| L3 precinct128 | 11,929 | 50.0 | 100% / 100% | 48.1 / 44.2 dB |

L1 -> L6 is a real ladder at constant bytes: decode rate 97% -> 100% and +6 dB
degraded fidelity, both monotone. **But it spans only 2.2x**, and resilience
SATURATES at L3-L6 while compute keeps climbing -- L12 costs twice L6, is worse
at one loss level and better at the other, and earns nothing. Declaring L1/L6/L12
would buy a 4.3x span with a top rung no one benefits from, which is the
"no real difference between rungs" failure this ladder exists to avoid.

Tiling is counterproductive and worth recording: `-t 112,112` LOWERS the decode
rate (87%/75% against 100%/97% plain), because a lost frame carrying a tile
header destroys that entire tile. Independent decodability and erasure
robustness are not the same property.

**Four mechanisms, two budgets, one conclusion.** J2K cannot span compute at
fixed bytes. The paper's 39x encoder ladder is reachable only through JPEG XL's
effort knob (48.9x measured on the board), so one codec can be the default or
the wide ladder, not both.

---

# QAT silently cost segmentation a 55x accelerator speedup

## The symptom

Segmentation costs 48 ms/tile for a 74 kB model, while classification costs
16 ms/tile for a 5 MB one -- three times the per-tile cost for a model twenty
times smaller. Under realistic retention it is **50-64% of the pipeline**, and
its 2.45x ladder is what pins the whole system near 2.5x.

It is not the post-processing. Differencing `mask_lowpass=5` against `=0` puts
the 5x5 Gaussian at **0.54 ms/tile, 1.1%** of the total. The hypothesis that
the fill dominated was wrong.

## The cause

Toggling the delegate off is the measurement that matters:

| | NPU on | NPU off | speedup |
|---|---:|---:|---:|
| classification `qat_a050r224` | 6.79 ms/tile | 62.35 | **9.2x** |
| segmentation `seg_low` (QAT) | 48.77 ms/tile | 48.52 | **1.0x** |
| paper's `custom_tiny_conv_unet` | **5.17 ms/tile** | 134.32 | **26x** |
| paper's `mobilenetv2_14_unet` | 34.0 ms/tile | (too slow to finish) | -- |

**Our segmentation models were never on the NPU.** The paper's were. Op counts
say why:

| model | DELEGATE partitions | TRANSPOSE | QUANTIZE |
|---|---:|---:|---:|
| `custom_tiny_conv_unet` (paper, PTQ) | **1** | 0 | 2 |
| `mobilenetv2_14_unet` (paper, PTQ) | **1** | 0 | 2 |
| `qat_cloud_p_w08d3_r128` (ours, QAT) | 4 | **3** | 5 |
| `qat_cloud_g_a10_r224` (ours, QAT) | 6 | **5** | 6 |
| `tf29_cloud_*` (ours, PTQ) | -- | **0** | 2 |

Exactly one TRANSPOSE per TRANSPOSE_CONV, present in the QAT builds of our
architectures and absent from the PTQ builds of *the same architectures*.
**tfmot wraps Conv2DTranspose in a form the converter cannot fold**, leaving a
runtime transpose at every decoder stage that splits the delegate partition.
TRANSPOSE_CONV itself is fine -- the paper's models use it and still compile to
one partition.

## QAT was buying nothing here

| model | QAT IoU | PTQ IoU | delta |
|---|---:|---:|---:|
| `g_a10_r224` | 0.588 | 0.567 | -0.021 |
| `g_a035_r096` | 0.473 | **0.690** | **+0.217** |

...and the PTQ numbers are a FLOOR, because `convert_tf29.py` calibrated cloud
models on EuroSAT land-cover JPEGs (`training/*/*.jpg` under `EUROSAT_DIR`) --
wrong content and wrong radiometry. A `representative_cloud()` path now
calibrates on 38-Cloud through `train_cloud.load_split`, so the transform chain
is the same object rather than a copy that can drift.

**Classification is the opposite case and keeps QAT**: post-training
quantisation costs it 19.8 points at alpha=1.4 (78.4% against 98.17%). The two
stages need different export paths, and that is a finding, not an inconsistency.

## Verified by smoke test, no training required

Graph topology and delegate partitioning depend on the architecture and the
export path, not on the weights, so an untrained model settles it in minutes:

    ops: {QUANTIZE: 2, CONV_2D: 39, DEPTHWISE_CONV_2D: 17, ADD: 10,
          TRANSPOSE_CONV: 5, CONCATENATION: 4, LOGISTIC: 1, DELEGATE: 1}
    TRANSPOSE=0  dynamic=0

| `smoke_g` = PTQ + batch_size=1, alpha=1.0 @224 | ms/tile |
|---|---:|
| NPU on | **22.30** |
| NPU off | 1245.69 |
| **speedup** | **55.7x** |

Both defects clear at once, and 22.3 ms/tile is **2.2x faster than the model
currently deployed** while being a far more capable architecture.

⚠️ **Cold-start graph compile is 13 s** (EXEC 15,279 ms against 2,212 ms of
module time). The module sets `cache_file_path`, so it amortises -- but
`latency_cost` must be taken from WARM runs or it is wrong by an order of
magnitude.

⚠️ **Converter version is unvalidated for accuracy.** The smoke model was
converted with TF 2.21 and has untrained weights, which cannot detect the
failure `convert_tf29.py` documents: a mismatched converter yields a model that
loads, allocates, runs, and returns the same class for every input. The real
export goes through the TF 2.15 container and must be checked against host
predictions on device.

## Why the float weights have to be retrained

Every `cloud_*.json` reads `qat: true` -- the float weights were overwritten by
the QAT runs, and the timestamps show it: `tf29_cloud_g_a10_r224.tflite` is
09-09 17:07 while `cloud_g_a10_r224.weights.npz` is 09-09 21:27, four hours
later. The existing PTQ exports came from float weights that no longer exist,
so PTQ needs a float retrain rather than a re-conversion.
`tools/retrain_seg_float.sh` does that and refuses to push any model that does
not verify `TRANSPOSE=0, dynamic=0`.

---

# The segmentation fix, validated end to end

## The diagnosis was a sensor mismatch, and it is now measured

The cloud segmenter was trained on 38-Cloud (**Landsat-8**) and deployed on
**Sentinel-2** imagery. CloudSEN12+ L1C removes that: same sensor, top-of-
atmosphere like the payload, and four classes rather than two.

A model trained for **3 epochs on 200 scenes**, val_iou 0.24 -- deliberately
weak, as a probe rather than a candidate:

| frame | old model (38-Cloud) | new model, host | new model, **board** |
|---|---:|---:|---:|
| koge-coast-full-clouds | **0.54%** | 96.58% | **97.31%** |
| koge-coast-no-clouds | -- | 0.03% | **0.03%** |
| amager-light-clouds | -- | 8.14% (per-tile 0-81%) | -- |

Host and board agree to **0.73 pp** on the cloudy frame and exactly on the clear
one. The clear frame is the control that matters: a model predicting "cloud
everywhere" also scores 97% on the first. And the light-cloud frame varying
0-81% *across tiles* is the thing the old model never did -- it tracks content.

**At 4.5-5.2 ms/tile on the NPU**, against 48.77 for the deployed model: a 10x
speedup from a model that also works.

## Two defects had to be fixed, and only one was expected

**QAT cost the accelerator entirely** (documented above): tfmot's
Conv2DTranspose wrapping leaves a runtime TRANSPOSE per decoder stage, splitting
the delegate partition four ways. Float + PTQ compiles to `DELEGATE: 1`.

⚠️ **The input-quantisation contract was the unexpected one, and it presented as
a total failure.** The module writes the raw uint8 tile into the input tensor,
so the model receives `(v - zp) * scale`; only `scale=2/255, zp=128` makes that
equal the `v/127.5 - 1` the model trained on. Calibrating PTQ on real device
tiles -- obviously the right distribution -- gave `scale=0.005075, zp=171`,
because the board cannot emit brighter than 182/165/164 and those tiles span
only [-0.87, 0.43]. Under passthrough a pixel of 182 then arrives as **0.056
instead of 0.427**, the range collapses, and nothing crosses threshold:

    host   96.58% masked        board   0.00% masked

**A better-fitted, tighter quantisation range is fatal here.** That is the
opposite of the usual intuition, which is why it was not obvious.

**It was identified by comparison, not instrumentation**: NPU and CPU returned
*identical* 0.00%. A delegate fault differs between two different processors;
only something upstream of both can be bit-identical on each. `--calib device`
now yields one synthetic full-range ramp before the real tiles, pinning the
endpoints while the rest keep interior activation statistics honest.

**Worth fixing properly before the campaigns**: the module should apply the
model's declared quantisation, `q = round((v/127.5 - 1)/scale + zp)`, which is
the identity when `scale=2/255, zp=128` and therefore backward compatible with
every existing model. Today a converter choosing a legitimate quantisation
produces a model that loads, allocates, runs, and reports nothing.

## What it changes upstream

Segmentation was 50-64% of the pipeline under realistic retention, with a 2.45x
ladder -- that is what pinned the whole system near 2.5x. At ~5 ms/tile it
becomes a minor term and classification's 8.5x ladder should dominate. The
encoder-span question, which consumed most of a day, applies to a stage that is
13-24% of the work at realistic retention.

⚠️ **These are agreement and plausibility checks, not a device IoU.** There are
no per-pixel segmentation labels on device imagery -- `device_labels.json` is
per-tile classes for the classifier. Before the paper quotes a segmentation
quality figure, device tiles need hand-labelling the way that file was built.

---

# The classifiers were fed the wrong rendering, not trained wrong

## What EuroSAT's RGB actually is

Measured per class, EuroSAT's distributed JPEGs against EuroSAT-MS reflectance
(same image, both forms, `Forest_1000`):

| class | JPEG | L1C reflectance | ratio |
|---|---|---|---:|
| Forest | 36 / 61 / 72 | 376 / 648 / 774 | 0.096 / 0.094 / 0.093 |
| SeaLake | 31 / 49 / 77 | 320 / 518 / 823 | 0.097 / 0.095 / 0.094 |
| Residential | 97 / 93 / 99 | 1043 / 992 / 1066 | 0.093 / 0.094 / 0.093 |
| AnnualCrop | 153 / 143 / 134 | 1650 / 1532 / 1443 | 0.093 / 0.093 / 0.093 |

**EuroSAT RGB = reflectance x 0.093.** A single uniform gain, no offset, no
atmospheric correction, constant across classes and channels to three decimals.

⚠️ **A wrong claim recorded earlier in this session, and why it was wrong.** I
concluded EuroSAT was atmospherically corrected because its CORPUS mean is
red-dominant (126.3/118.1/115.8) while L1C reflectance is blue-dominant. That
comparison is invalid: the corpus mean is red-dominant only because bright
classes (AnnualCrop, Residential) dominate the average. Per class the JPEG
preserves reflectance's blue-dominance exactly -- Forest is 36/61/72. This is
the same error as the anchored-affine fit earlier: **comparing pooled statistics
across two corpora with different content composition**. Twice in one day.

## The device transform is the defect

| | R | G | B |
|---|---:|---:|---:|
| EuroSAT rendering | 0.093 | 0.093 | 0.093 |
| device `calib_gain` | 0.0403 | 0.0314 | 0.0252 |
| device `calib_offset` | 17.27 | 36.86 | 60.98 |

Less than half the gain, non-uniform across channels, and offset-shifted. Two
consequences: everything is too dark (AnnualCrop renders at 83 where EuroSAT
gives 153), and at low signal the offsets dominate so dark vegetation turns
blue -- which is what water looks like. Hence Forest classified as SeaLake, and
tree recall of zero that no gain-only or brightness correction could repair
alone.

## Both fixes together, measured

202 clear expert-verified tiles, coarse-group agreement:

| | agree | tree | water |
|---|---:|---:|---:|
| a050 device affine | 27.2% | 1.6% | 100.0% |
| a050 EuroSAT gain | 41.6% | 22.2% | 96.4% |
| **a050 EuroSAT gain + blur64** | **56.4%** | 23.8% | 82.1% |
| a140 device affine | 30.7% | 0.0% | 92.9% |
| a140 EuroSAT gain | 36.1% | 0.0% | 85.7% |
| **a140 EuroSAT gain + blur64** | **57.9%** | 23.8% | 85.7% |

**Roughly double, with no retraining.** The second change reproduces the 64->224
upscaling blur the models trained on, since EuroSAT patches are 64px and the
device tile is a sharp native 224.

⚠️ **The two fixes are NOT independent, and testing them singly was misleading.**
The alpha=1.4 rung gained +5.4 from gain alone and 0.0 from blur alone, but
+27.2 from both -- it resisted every single-variable correction and looked
broken in a way the alpha=0.5 rung did not. Any one-at-a-time sweep would have
concluded the wide rung was unsalvageable.

## What this costs and does not cost

Both are CONFIG changes to parameters that already exist (`calib_gain_*`,
`calib_offset_*`), so no rebuild and no retrain. The existing weights are fine;
they were being shown the wrong pixels.

Still open at ~57% agreement: something beyond radiometry and scale remains.
Candidates are WorldCover's own accuracy as ground truth (it is a 10 m model
product, not expert labels), the fact that `built` is n=6 so Residential is
still unmeasured, and EuroSAT's saturated ~2-point accuracy range. EuroSAT-MS
is fetched and split-aligned (21,600/5,400, zero missing) if a retrain in the
device's exact domain turns out to be wanted -- it is no longer on the critical
path.

## Sensitivity on hardware: PASSED

Two device frames, `qat_a140r224`, all 99 tiles each, after the rendering fixes
(gain 0.0932 uniform / zero offset in demosaic, `train_res=64` on slots 3/4/5):

| class | Koge (farmland + coast) | Amager (city + airport) |
|---|---:|---:|
| PermanentCrop | **35** | 19 |
| Forest | **32** | 1 |
| Industrial | 13 | 13 |
| Highway | 9 | **20** |
| Residential | 5 | **32** |
| SeaLake | 3 | **12** |
| AnnualCrop | 2 | 1 |

Residential 5 -> 32 and Forest 32 -> 1 between the scenes. Both decompositions
match the imagery: Koge is patchwork fields with heavy woodland, Amager is dense
housing plus Kastrup's runways (Highway 20) and terminals (Industrial 13). This
is the first time the pipeline has discriminated scene content, and it comes
from two config changes with no new weights.

It also settles `class_index: 7`, which CloudSEN12 could not (built was n=6
there): the retention mechanism has real signal, 32 tiles on urban imagery
against 5 on farmland.

⚠️ **Two corrections to my own earlier reporting, both from truncated output.**
I described Koge as "the sea scene" and its crop/forest predictions as a
failure -- it is predominantly farmland, so those predictions were correct. And
I reported "SeaLake never appears" from reading `head -n 4` of my own histogram;
the full histogram has SeaLake at 12 on Amager, proportionate to the water in
frame. Filtering my own diagnostics has now misled me three times in this
session (truncated console log, self-matching pgrep, truncated histogram).

⚠️ **Water is the real weakness.** Koge is roughly one third sea and gets only 3
SeaLake tiles against an expected 25-30. Amager's 12 is about right. This
matches the CloudSEN12 result -- water recall high, precision poor -- and water
is the class the mission cares about. Part may be tiling: the sea third is ~3
columns of an 11x9 grid and a diagonal coastline makes many of those mixed
land-water tiles, which legitimately are not SeaLake.

## ⚠️ The two stages cannot currently share one input

There is ONE demosaic stage, so classification and segmentation consume
identical tiles -- and the gain that fixes classification breaks segmentation:

| class | reflectance | EuroSAT gain 0.0932 | old device affine |
|---|---|---|---|
| clear | 1916/1641/1618 | 150.5/142.3/138.8 | 91.4/87.0/100.6 |
| thin cloud | 2421/2095/2132 | 181.0/173.2/177.4 | 110.1/100.5/112.8 |
| thick cloud | 3351/3069/3160 | 214.8/212.6/217.5 | 147.6/132.5/140.0 |
| | **saturated** | **51.8% of thick cloud** | 4.4% |

At 0.0932 half of all thick-cloud pixels clip at 255, and 10.5% of clear ground
does too. The CloudSEN12 segmentation rungs measured at IoU 0.747 were trained
through the OLD affine, so they are not valid under the new one.

Re-rendering per stage does not work: once thick cloud is clipped, no downstream
affine recovers it. The pipeline needs ONE rendering with BOTH stages trained in
it, and the gain becomes a joint design parameter -- high gain gives
classification contrast and destroys cloud, low gain preserves cloud and darkens
vegetation. It should be swept and chosen on measurement, not inherited from
EuroSAT because EuroSAT happened to use it.

## The joint rendering: gain 0.060, chosen by sweep

| gain | cls a050 | cls a140 | sep clear/thick | sep thin/thick | sat thick | sat clear |
|---:|---:|---:|---:|---:|---:|---:|
| 0.040 | 54.5% | 51.5% | 1.224 | 0.826 | 5.8% | 0.9% |
| 0.050 | 53.5% | 53.5% | 1.301 | 0.879 | 14.0% | 1.2% |
| **0.060** | **55.0%** | 52.5% | 1.364 | **0.895** | 23.2% | 2.2% |
| 0.070 | 54.0% | 55.0% | 1.409 | 0.883 | 32.4% | 3.9% |
| 0.080 | 53.5% | 56.4% | 1.431 | 0.844 | 40.7% | 5.7% |
| 0.0932 | 56.4% | 57.9% | 1.418 | 0.764 | **50.7%** | 8.5% |

Scored asymmetrically on purpose. Classification is measured DIRECTLY -- the
models are fixed and their weights do not depend on the rendering. Segmentation
cannot be, since its models were trained through a particular affine and would
need retraining per candidate, so it is scored by what a retrained model could
exploit: 8-bit class separability, plus saturation as a hard veto rather than a
term in a score.

**There is no real tension.** Classification moves 3-6 points across the entire
range against ~3.5 pp standard error. The large gains won earlier came from
removing the offsets and matching training resolution, not from gain magnitude.
Segmentation's thin/thick separability peaks at 0.060 and collapses by 0.0932.

Cost stated: alpha=1.4 gives up ~5 points (57.9 -> 52.5), ~1.5 standard errors.
Limit stated: 23.2% of thick cloud still clips at 0.060, so degrees of thick
cloud stay indistinguishable.

---

# Completion plan, Phase 0: rendering frozen at gain 0.050

Two plain U-Nets (w08d3 @96), 12 epochs on 1500 scenes, at gain 0.050 and
0.060; PTQ-exported with matching calibration; scored on the **validation**
split against expert masks. The 3-epoch smoke passed first (val_iou 0.409,
affine confirmed in the log) so no long run went in blind.

| gain | float val_iou | PTQ IoU | precision | recall | FP on clear |
|---:|---:|---:|---:|---:|---:|
| 0.050 | 0.6944 | 0.6282 | **0.8467** | 0.7089 | **5.45%** |
| 0.060 | 0.6887 | **0.6393** | 0.8163 | **0.7468** | 6.66% |

**Tied within noise.** The separability proxy in the gain sweep predicted 0.060
clearly ahead; real IoU says +0.011, smaller than run-to-run variance, with the
two trading recall for precision. Phase 0 existed to check that proxy and the
proxy was wrong about the margin.

**Frozen at 0.050** on secondary criteria that both point the same way: thick-
cloud saturation 14% against 23% (a veto, not a trade-off), and lower false
positives on clear ground (the mission-asymmetric error). Classification is
indifferent.

Tooling fixed on the way: `cs12_validate.py` and `convert_tf29.py` had the
*old* affine hardcoded and would have silently mis-rendered any model trained
at a different gain; both now take the model's affine, and `train_cloud.py`
records it in the metadata so nothing has to guess. `cs12_validate.py` also
selects on `validation` by default now.

**Everything measured before this point at a different rendering is invalid
for the pipeline as configured.** That is stated here so it does not have to be
rediscovered.

## Phase 1 — classification retrained on EuroSAT-MS (2026-09-13)

Rendering frozen at gain 0.050 (Phase 0). `training/train_landcover_ms.py`
renders the 27,000 raw L1C images exactly as `module_1.yaml` does
(`clip(refl × 0.050, 0, 255)`, [-1,1] in tf.data, no Rescaling layer), keeps
the three deployed architectures and the filename-matched 21,600 / 5,400
split. Every export is scored by `analysis/eurosat_ms_holdout.py` as raw uint8
through the TF 2.9.1 flatbuffer, contract `(2/255, 127|128)` asserted.

**Reported quality — EuroSAT-MS `testing`, 5,400 images, top-1:**

| rung | float (Keras) | PTQ tflite | **QAT tflite** |
|---|---:|---:|---:|
| low `custom_conv` @224 | 75.39 | 72.67 | **75.13** |
| α=0.5 @224, trained to exhaustion | 98.59 | 95.13 | 98.57 |
| high α=1.4 @224 | 98.57 | 96.19 | **98.52** |

- QAT beats PTQ by 2.4–3.4 pp on every rung → the rungs ship as QAT.
- **α=0.5 trained to exhaustion equals α=1.4** (98.57 vs 98.52). The medium
  rung is therefore an *operating point*, as it was in the paper's own ladder
  (89.98 % was a target stop): `STAGES=med` trains α=0.5 to val 0.90 with
  quarter-epoch snapshots, then QAT to the same target. Snapshots of every
  epoch are kept under `training/out/snap/` so the point can be moved without
  retraining.
- Local CPU smokes (custom_conv, 2,000 images, 3 epochs) validated the loader,
  both export paths and the holdout scorer before any GPU time. Two traps
  found there: the npz is class-ordered (a plain head of the test split is one
  class), and ImageNet BatchNorm statistics at momentum 0.999 leave val at
  chance for hundreds of steps (set to 0.99).
- Pre-rendering at 224 px float32 is 11 GB and OOM'd the 2070; the resize now
  happens per batch in `tf.data`.

**End-to-end viability — CloudSEN12 `validation`, 465 tiles (535 scenes,
2×2 grid of 224 crops, ≥60 % one WorldCover group, ≤10 % cloud by expert
mask), frozen rendering, the module's exact 224→64→224 resize chain:**

| rung | coarse agreement | water recall | water precision | tree recall / precision |
|---|---:|---:|---:|---:|
| low QAT | 39.4 % | 78.3 % | 71.4 % | 25 / 92 |
| α=0.5 exhausted QAT | 59.6 % | 91.6 % | 87.4 % | 36 / 92 |
| high QAT | 59.4 % | 92.8 % | 78.6 % | 35 / 97 |

The target class (water, `class_index` 9 = SeaLake) is found at every rung.
Overall agreement is capped by taxonomy, not the model: WorldCover "tree
cover" includes sparse woodland that EuroSAT calls Herbaceous/Pasture (tree
precision 92–97 % with recall ~35 %). `built` has n=12 in the whole split —
too few to evaluate Residential, which is one more reason the target is water.
A/B without the 64-px chain: 55.3 / 43.2 % agreement, water precision 70–74 %
→ `train_res: 64` confirmed.

**Medium operating point landed** (`ms_medop`: α=0.5 stopped at val 0.9037
after quarter-epochs, QAT'd to val 0.9167): PTQ 86.56, **QAT 92.09** on the
holdout. The final ladder, all QAT, deployed format:

| rung | holdout top-1 | CS12 coarse agreement | water recall / precision |
|---|---:|---:|---:|
| low `custom_conv` | **75.13** | 39.4 % | 78.3 / 71.4 |
| med α=0.5 @0.90 | **92.09** | 49.5 % | 96.4 / 82.5 |
| high α=1.4 | **98.52** | 59.4 % | 92.8 / 78.6 |

Monotone on both corpora; steps +16.96 and +6.43 pp against σ≈0.4 pp
(n=5,400). Water recall/precision differences between med and high are inside
noise at n=83. Remaining exit item: latency and energy re-confirmed on the
board for the three new flatbuffers (QAT graphs differ from the previous
exports only in quantisation parameters, but that is measured, not assumed).

**Board cost, same session (idle 2.7486 W, n=47 of 50, `dev_cls_ms.json`):**

| rung | EXEC median | of which resize | µWh/exec |
|---|---:|---:|---:|
| low | 659.7 ms | ~320 ms | −29 → below the floor; **estimate 87** (medop × 601.7/998.4) |
| medop | 1077.1 ms | ~320 ms | 145 |
| high | 2028.6 ms | ~320 ms | 747 |

⚠️ **The previous hints (246.7 / 746.6 / 1691.7 ms) were wrong for the config
as committed.** They were measured through the bench's own module config,
which had no `train_res`, while the flight config applied `train_res: 64`.
The module log brackets the resize: `Copying → Copied` 3.3 ms/tile, i.e.
~320 ms per 99-tile frame, on top of `Invoking → Invoked` 15.5 ms/tile for
α=1.4. 224→64 is a non-integer 3.5× scale, so `INTER_AREA` takes OpenCV's
generic path (host, single thread: 0.29 ms at 3.5× vs 0.11 ms at 4×).

**`train_res: 56` taken instead** — an integer 4× scale. Host-side A/B on the
CloudSEN12 gate (`--train-res`): 56 scores 40.2 / 49.5 / 60.9 % agreement
(64: 39.4 / 49.5 / 59.4; water recall 78 / 98 / 92 %), while 112 loses 4–7
points on low and high. Re-measured on the board in the same session
(`dev_cls_ms_tr56.json`):

| rung | EXEC median (64 → 56) | µWh/exec (64 → 56) |
|---|---:|---:|
| low | 659.7 → **497.8** ms | −29 → −92 (below floor; **estimate 47** = medop × 437.5/774.5) |
| medop | 1077.1 → **850.5** ms | 145 → **83** |
| high | 2028.6 → **1819.6** ms | 747 → **710** |

Latency span 3.7× (8.5× when the resize was omitted altogether). The medium
rung's energy moving 145 → 83 between two same-session runs puts the
per-execution energy noise at n=50 around ±60 µWh — the ordering is
unaffected, the absolute medium value should be quoted with that spread.

`flight-config/module_3/4/5.yaml` repointed to
`/usr/share/models/landcover_ms_{low,medop,high}.tflite` (pushed,
md5-matched), `class_index: 9`, `train_res: 56`, costs above. **Phase 1
closed.**

Also fixed on the way: a failed serial push leaves the board's `dd` waiting
for its byte count in raw mode and every later push is swallowed as data —
three models were silently "SKIPPED" and the run said ALL DONE. `bench_models`
now exits non-zero on a skipped model; Ctrl-C on the console frees it.

Files: `phase1_cls_holdout.json`, `classifier_validation_ms.json`,
`dev_cls_ms.json` (`classifier_validation_ms_prelim.json` has the exhausted
α=0.5 for the record).

## Phase 2 — segmentation retrained at gain 0.050 (2026-09-13) · CLOSED

Four candidates retrained at the frozen rendering (`tools/phase2_seg_train.sh`,
float; CloudSEN12 train split, `--affine gain:0.050`), exported under TF 2.9.1
with CloudSEN12 calibration at the model's own gain and the ramp-pinned
contract (`tools/phase2_seg_export.sh`), all `TRANSPOSE=0 · dynamic=0 · int8`.
Quality is IoU against expert masks on the **validation** split (535 scenes,
device-footprint tiles, `analysis/cs12_validate.py`), which now also scores
float graphs (`--models '*.weights.npz'`) and bootstraps adjacent-rung
differences over scenes.

**PTQ is not neutral on the MobileNet-encoder U-Nets:**

| candidate | float IoU | PTQ IoU | PTQ loss | FP on clear (PTQ) |
|---|---:|---:|---:|---:|
| plain w08d3@96 | 0.646 | 0.639 | 0.007 | 2.9 % |
| plain w16d3@128 | 0.743 | 0.730 | 0.012 | 4.6 % |
| MNv2 α=0.35@160 | 0.794 | **0.641** | **0.153** | **26.8 %** |
| MNv2 α=1.4@224 | 0.811 | 0.742 | 0.069 | 5.5 % |

Calibration size and seed do not fix it (400 samples: 0.668; seed changes
nothing because 150 samples already uses every calibration sample). Same
pattern as PTQ on the classification MobileNets, where QAT fixed it — but full
QAT leaves runtime TRANSPOSE ops from tfmot's `Conv2DTranspose` wrapping and
pushes the model off the NPU, and `UpSampling2D` is out because the VX delegate
computes `RESIZE_NEAREST_NEIGHBOR` wrongly on this board.

**Selective QAT** (`train_cloud.qat_wrap(mode="selective")`, shared with the
converter): every layer *except* `Conv2DTranspose` is annotated, so tfmot never
wraps those; the converter calibrates them from the representative set. The
export keeps `TRANSPOSE_CONV` (5), `TRANSPOSE=0`, all int8 — verified on the
host before any GPU time. 8 epochs at lr 1e-4 from the float weights:

| rung | float | PTQ | **selective QAT** | prec / recall | FP on clear |
|---|---:|---:|---:|---:|---:|
| MNv2 α=0.35@160 | 0.794 | 0.641 | **0.789** | 0.914 / 0.853 | 0.67 % |
| MNv2 α=1.4@224 | 0.811 | 0.742 | **0.811** | 0.904 / 0.887 | 1.43 % |

**Board, warm (one cold run discarded: VX compile 11.5 s / 23.3 s), n=50,
idle 2.7470 W, koge-coast-full-clouds (`dev_cs12_g050.json`):**

| rung | val IoU | EXEC warm | ms/tile | µWh/exec |
|---|---:|---:|---:|---:|
| plain w08d3@96 | 0.639 | 565.8 ms | 5.1 | −71 → below floor; **estimate 98** (medium × 506.0/1344.8) |
| plain w16d3@128 | 0.730 | 868.4 ms | 8.1 | 183 |
| **MNv2 α=0.35@160 sel. QAT** | 0.789 | 1430.6 ms | 13.6 | 261 |
| **MNv2 α=1.4@224 sel. QAT** | 0.811 | 4158.1 ms | 40.3 | 2015 |

Adjacent steps, paired scene bootstrap (95 % CI): 128−96 +0.091 [+0.073,
+0.111]; α0.35−128 +0.059 [+0.042, +0.077]; α1.4−α0.35 **+0.021 [+0.009,
+0.034]** — all above noise.

**Ladder chosen: 96 → α=0.35@160 → α=1.4@224** (slots 6 / **19** / 7;
pipeline 1 now carries `medium` for segmentation; `MAX_MODULES` is 20 and 19
was free). w16d3@128 is dominated by α=0.35 on every quality axis and is
*worse than the low rung* on precision (0.854 vs 0.885) and FP-on-clear
(4.63 % vs 2.89 %) — a rung that discards more cloud-free ground than the one
below it is not a rung. The top step buys +0.021 IoU for 2.9× latency and
7.7× energy: reported as the measured value of the top effort level, not tuned
away. Models on the board at `/usr/share/models/cs12g050*.tflite`,
md5-matched.

Two harness defects fixed on the way: (1) after any **baseline** run there is
no module log, and `grep PATTERN $L` with `L` empty read the *console* as stdin
forever — every later push was typed into that grep ("vmem push failed 3×",
twice). Guarded, and every board command now runs with stdin closed.
(2) `bench_models` exits non-zero when a model is skipped.

## Phase 3 — encoder rungs on the corpus, bytes frozen (2026-09-14) · CLOSED

Rule added by the user: **the byte budget is frozen across effort levels**
within a codec (it may differ between codecs), so effort trades compute for
quality and nothing else. Budget taken: **3,000 B/tile corpus mean** for both.

**Corpus and tooling.** `analysis/encoder_corpus_eval.py`: 300 retained
CloudSEN12 validation tiles as the encoder receives them (224 px crops at
gain 0.050, >50 % clear by expert mask, cloud pixels replaced by the
segmentation module's 5×5 Gaussian fill). JXL: effort 1–9 × 9 distances, PSNR
interpolated per tile at fixed bytes so efforts compare iso-rate and paired.
J2K: budgets 3/6/10 kB × layers 1–8, rate bisected per config, degraded-mode
PSNR with 1 and 2 CCSDS frames (512 B) lost averaged over 12 drop patterns.
**Codec versions are the board's**: libjxl 0.10.2 (imagecodecs 2024.1.1 in a
dedicated venv — 0.12 has a different effort structure) and openjpeg 2.4.0.
Host and board bytes agree to <2 % on the koge-coast-no-clouds frame for all
eight configs measured (`analysis/enc_host_vs_board.py`), after fetching the
board's own encoded tiles out of `images.vmem` and decoding them here
(tile 0 ↔ host tile 0 at 32.7 dB).

**JPEG 2000 — quality layers, 3 kB, resync on, res 6** (corpus; board
koge-no-clouds, 3 launches × 4 batches, idle 2.7466 W):

| layers | clean | **1 frame lost** | 2 lost | decode (1 lost) | ms/tile | µWh/exec |
|---|---:|---:|---:|---:|---:|---:|
| **1** | 41.39 | **32.51** | 31.23 | 90 % | 43.3 | 421 ± 28 |
| **2** | 41.17 | **36.04** (+3.35 [+3.03, +3.66]) | 32.63 | 90 % | 55.1 | 555 ± 12 |
| **3** | 40.93 | **37.88** (+1.79 [+1.64, +1.95]) | 33.24 | 91 % | 68.7 | 686 ± 16 |
| 4 | 40.69 | 37.42 (−0.46) | 33.96 | 91 % | 83.8 | 848 ± 10 |
| 5 / 6 / 8 | 40.46 / 40.19 / 39.61 | 36.71 / 36.12 / 33.80 | 33.94 / 34.91 / 32.48 | | | |

The same shape at 6 kB and 10 kB: one-frame-lost PSNR peaks at **three
layers** and falls from four on (signalling out of a fixed budget). Ladder
**L1 / L2 / L3** (slots 16/17/18): monotone on all three axes, energy steps
+134 and +131 µWh against ≤ ±28. Clean PSNR is mildly inverted (−0.46 dB over
the ladder) — the robustness-not-fidelity statement stands. JXL under the same
loss decodes 9.8 % (e1) / 0 % (e5, e8) of tiles (100 tiles × 4 patterns).

**JPEG XL — effort at 3 kB** (PSNR on the 259 tiles inside every effort's
curve; board as above):

| effort | PSNR @ 3 kB | vs previous | ms/tile | µWh/exec |
|---|---:|---:|---:|---:|
| 1 (=2) | 40.22 | | 25.9 | 251 ± 17 |
| **3** | 40.49 | +0.27 [+0.26, +0.28] | 27.1 | 264 ± 6 |
| 4 | 40.43 | −0.06 | | |
| **5** | 40.93 | +0.44 [+0.37, +0.52] vs e3 | 200.4 | 2522 ± 8 |
| 6 / 7 | 40.83 / 40.82 | −0.10 / −0.11 vs e5 | (old: 263 / 290 ms/tile) | |
| **8** | 40.99 | +0.06 [+0.04, +0.07] vs e5 | 934.1 | 3982 ± 4 |
| 9 | 40.97 | −0.02 vs e8 | (old: 1300 ms/tile) | |

Three quality tiers ({1,2} < {3,4} < {5–9}) against three cost tiers ({1–4} ≪
{5–7} ≪ {8,9}); the e8/e9 tier's gain at iso-rate is 0.06–0.11 dB at ≤ 3 kB
and zero at 4 kB. Ladder **e3 / e5 / e8** (slots 11/12/13), distances 2.358 /
2.884 / 2.590: monotone on all three axes, every step above noise. Two
decisions stated plainly: e3 rather than e1 as the low rung because e1 is
*dominated* (e3 is +0.27 dB at an energy difference inside the launch noise);
and the top rung's measured value is +0.06 dB for 4.7× latency and 1.6× energy
— that is what JPEG XL's top effort buys at fixed bytes, reported as such.

**Two harness defects fixed on the way:** `run.sh` stops when the completion
count is stable for `min_drain` seconds, so an encoder slower than that per
batch (e8 ≈ 100 s) was cut after one batch (`duration_s 121, n_completed 1`) —
`DRAIN` is now 160 s; and a transient 30 s serial timeout in the completion
poll aborted a launch — the poll now tolerates it. Files: `encoder_corpus.json`
(J2K + JXL-0.12), `encoder_corpus_jxl010.json` (JXL, board version),
`jxl_distances_3k.json`, `dev_enc_g050{,_r2,_r3}.json`, `_summary.json`,
`dev_enc_g050_fullcloud_frame.json` (the discarded full-cloud attempt).

## The device frames are retired; every board run is on CloudSEN12+ (2026-09-14)

`training/make_cs12_frames.py` packs CloudSEN12+ crops into the payload's raw
format — 2464×2056 Bayer RG, 16-bit LE containers, 99 tiles in the tiler's
11×9 grid, pre-rotated and mosaicked with the phase that reproduces the
demosaic module's channel order through its own OpenCV chain (42.5 dB against
12–17 dB for the other three phases). With the frozen rendering the
demosaiced tile matches the corpus rendering at ~37 dB mean (bilinear
demosaic interpolation — the cost a real Bayer sensor pays). 21 validation +
39 test frames, 601 MB, `~/dipp-datasets/cs12-frames/`, each with `<frame>.json`
(per-tile scene, crop, thick/thin/shadow/clear fractions, WorldCover shares)
and `<frame>.labels.npz` (the 224×224 masks and land cover). Board tile k =
frame tile k, and the modules log decisions in tile order, so
`analysis/e2e_score.py` scores every board run against labels: hardware water
recall/precision, segmentation decision agreement and clear-fraction error,
uploaded-tile value.

The five `amager-*`/`koge-coast-*` frames are moved out of reach on host and
board and every tool default now points at `cs12_validation_00.bayerRG`
(`run.sh`, `bench_models`, `phase2/3/4` scripts, `device_labelled_eval.DATA`,
`enc_host_vs_board`); `rung_export_smoke.py` no longer computes any
device-frame figure. Transfer over serial is 17 min/frame — the first three
validation frames were pushed that way; the full set goes by USB stick.

## Decision 2026-09-14: JPEG 2000 is the flight encoder; JPEG XL has two levels

JPEG XL's effort curve at fixed bytes is a step function — {1–4} < {5–9} with
the exhaustive tier (8/9) worth +0.06 dB for 1.6× the energy — so a third JXL
level is not justifiable and is dropped. **JXL is now a two-level module**
(slot 11 = e3 low, slot 12 = e5 high; slot 13 keeps the e8 measurement, not
referenced). **JPEG 2000 becomes the encoder of the adaptive pipeline (1) and
of the static low/medium/high pipelines (2/6/3)**, slots 16/17/18: three
genuine levels whose top buys robustness on a CCSDS-framed link. Pipeline 5
is redefined as **adaptive with JPEG XL** (its old "demosaic + JXL" role is not
re-run), so the codec comparison is adaptive-vs-adaptive: pipeline 1 has
3 × 3 × 3 = 27 operating points, pipeline 5 has 3 × 3 × 2 = 18 (W7 said 18 for
the JXL pipeline; it is now the J2K one that has 27). The scheduler handles the
missing medium level without change (HIGH → MEDIUM returns NOT_FOUND → LOW).

## Phase 4 — end-to-end on CloudSEN12+ frames (2026-09-14) · required half CLOSED

**Retention on the corpus** (`analysis/pipeline_retention.py`, 2,140
validation tiles through the exact module chain; truly water-and-clear tiles
are 4.3 % of the corpus):

| classification | keeps | seg keeps of those (low / med / high) | reaches encoder | of which water & clear |
|---|---:|---|---:|---:|
| low | 4.3 % | 92 / 95 / 95 % | 4.0 % | 72–74 % |
| med | 19.6 % | 50 / 48 / 49 % | 9.3–9.9 % | 37–42 % |
| high | 14.9 % | 64 / 61 / 62 % | 9.1–9.6 % | 39–43 % |

**On the hardware** (`tools/phase4_e2e.py` + `analysis/e2e_score.py`, full
flight config, one frame per launch, frames `cs12_validation_00/01/02`):

| pipeline | water recall / precision | seg decisions agree w/ expert mask | uploaded genuinely water & clear | composed ms/frame |
|---|---|---|---|---:|
| 2 static low | 75/100 · 67/80 · 50/100 % | 100 · 88 · 100 % | 100 · 50 · 100 % | 1513–1731 |
| 6 static medium | 100/73 · 83/71 · 100/67 | 100 · 88 · 100 | 67 · 38 · 60 | 2625–2857 |
| 3 static high | 88/78 · 67/100 · 67/67 | 94 · 78 · 93 | 64 · 40 · 67 | 3786–4177 |
| 1 adaptive J2K | identical decisions to 3 (HIGH chosen at every stage: full battery, no pressure) | | | 3780–4183 |
| 5 adaptive JXL | identical decisions; encoder +1.3 s/frame (e5 vs L3 on ~11 tiles) | | | 4563–5577 |

Every stage composes on independent Sentinel-2 imagery through the real
demosaic; hardware water recall/precision and segmentation agreement are in
line with the corpus figures (the segmenter's clear-fraction MAE against the
expert mask is 0.02–0.15). The scheduler picking HIGH everywhere with a full
battery is the expected behaviour; the effort mix appears only under energy or
latency pressure, which is Stage 5's experiment.

**Pipeline span, measured** — superseded by the calibrated numbers below
(the first version of this table composed a 982 µWh demosaic + tile figure
that was an old config hint, not a measurement, and per-stage energies that
carried a baseline I/O bias; see *Energy, calibrated*).

Two harness fixes: `run.sh` grepped `tflite_binary_segmentation_*` for kept
pixels while the module logs `tflite_segmentation_*` (`kept_pixels.txt` was
always empty); predictions are read from the archived `predictions.txt`, 20
per line, because a 200-character line is split by the console. Files:
`pipeline_retention.json`, `e2e_runs.json`, `e2e_runs_scored.json`.

**Encoder costs re-measured on a CloudSEN12+ frame** (`dev_enc_cs12.json`,
2026-09-14, n=4): superseded by the n=20 bracketed-floor measurement in
*Energy, calibrated* below; the ladder shape and bytes (2,988 / 3,014 JXL,
3,051–3,057 J2K on the 3,000 B target) were already right, the absolutes were
short by the I/O term.


## Energy, calibrated (2026-09-15) — the low rung measured, the I/O bias removed

Every per-stage energy before this section was a difference against a
baseline run of demosaic + tile, and every one of them was biased by a term
nobody had isolated: **the baseline uploads all 99 raw tiles — 15 MB of
writes per frame — while a model run uploads only what it retains.** The
difference "model − baseline" therefore subtracted an I/O cost the model run
never paid, which is how the low classification rung measured −76 µWh and had
to be marked an estimate, and why demosaic + tile looked like ~1 mWh. A second
bias sat under it: **the idle floor is not one number.** After a boot the
board draws 3.03–3.30 W for 45–110 min, then 2.75 W (every long-uptime
session had shown 2.70–2.83 W); a floor read once per session and applied to
a run in the other state is off by 0.3 W, i.e. ~±300 µWh per 150 s run.

**Design** (`tools/energy_calibration2.sh`, results `energy_cal2/`,
summary `analysis/energy_cal_summary.py` → `stage_energy.json`): n=50 frames
of `cs12_validation_00` per run, an idle-floor reading immediately before
every run (and, from the encoder pass on, immediately after — the *bracket*:
a run whose two floors disagree by >0.03 W, or that crosses the 2.85 W band
edge, is invalid; the encoder-baseline run that straddled 3.08 → 2.75 W was
caught this way and redone). The subtraction chain, per 99-tile frame:

| term | how | µWh |
|---|---|---:|
| I/O, 15 MB upload | seg-low keep-all − seg-low keep-none (same model, threshold 0.0 vs 1.0) | **304** |
| demosaic + tile | baseline − I/O | **216** |
| classification low / med / high | run with `class_index 10` (keeps nothing, uploads nothing) − fixed | **219 / 394 / 849** |
| segmentation low | keep-none − fixed | **163** |
| segmentation med / high | keep-all − baseline (both upload 15 MB) | **496 / 2,486** |
| J2K L1 / L2 / L3 | n=20, bracketed; run − fixed − I/O pro rata for 0.3 MB (`phase3_enc_energy.sh`, `energy_enc/`) | **738 / 884 / 1,026** |
| JXL e3 / e5 | same | **573 / see below** |

Rep 1 of the chain straddled the floor transition (3.30 → 3.08 → 2.76 W
across its eight runs) and is excluded by rule, not by hand; rep 2 sits at
2.750–2.783 W throughout. Reproducibility where both reps were settled:
cls-high 1,075 vs 1,065 µWh/batch, seg-med 1,019 vs 1,017, seg-high 3,032
vs 3,007 — ≈1 %. The encoder pass's own baseline, measured a day later in a
different session, came out at 517.7 µWh/frame against the calibration's
520.8 (0.6 %).

**The numbers are now physically coherent**, which they were not before:
demosaic + tile draw 0.93 W above idle for 0.84 s; the classifiers 1.8 W
(NPU + CPU resize) for 0.44 / 0.78 / 1.68 s; segmentation-high 2.3 W for
4.0 s; and every encoder rung ≈0.5 W — one busy core — for 4.7 / 6.1 / 7.5 s
(J2K) and 2.7 s (JXL e3). The J2K ladder's energy span is **1.39×** at fixed
bytes, not the 1.65× the biased subtraction gave, because the I/O term was a
constant offset that inflated ratios.

**Direct measurement of the static pipelines** (`tools/phase4_e2e.py --n 50
--idle 60`, `e2e_static50.json` + `e2e_static50_state308.json`,
`analysis/e2e_energy.py`), two independent launches each — one in the 3.08 W
idle state, one in the 2.75 W state, floors bracketed within 0.02 W in both:

| pipeline | direct µWh/frame (3.08 W state / settled) | composed from the stage table with this frame's own retention | ms/frame |
|---|---:|---:|---:|
| 2 static low | **570 / 637** | 491 | 1,655 |
| 6 static medium | **884 / 830** | 808 | 2,707 |
| 3 static high | **1,625 / 1,619** | 1,582 | 4,204 |

Latencies are identical in the two states module by module (so the high-floor
state is not a clock difference); the energies differ by up to 67 µWh/frame
in either direction, which is the run-to-run spread of a 47-frame direct
measurement. The composition from single-stage benches sits 40–110 µWh/frame
below the direct figure: within that spread at medium and high, and a
consistent ~100 µWh short at the low rung — the per-frame cost of the two
extra module hops (fork, `.so` load, SHM, persistence) that the three-module
bench pipelines do not carry and that is a larger share of a cheap frame.
**Span high/low: 2.7× on frame energy** (was 1.96× with the old fixed-stage
hint), **2.5× on latency**, **5.0× on the ML + encoder work** (1,366 vs 274
µWh composed, fixed stages removed).

The 3.08 W state itself is not explained: it lasted 45 min after one boot and
110 min after another, ends abruptly, and the modules run at the same speed in
it. Every number in this section is bracketed, so it cannot bias a result, but
Stage 5's long runs should log which state the board is in (`P_idle` before
and after each run is now in every record).

Flight-config `energy_cost` hints for slots 1–7, 11, 12, 16–19 are written
from `stage_energy.json` / `encoder_energy.json` by
`tools/update_energy_hints.py`; nothing in the config is typed in.

**Two bench defects found on the way**, both of the kind this stage exists to
remove: `tests/board/run.sh` declared a run drained after 20 s without a new
completion, so JPEG XL e5 at 20.3 s/batch was cut off after 4 of 20 batches
with the other 16 ingested and never run (charged to 4 batches: 3.8 mWh
"per batch"); the drain now waits `MIN_DRAIN` seconds since the last change
in the count. And a hard hang — console dead on both ports, 60 s cut needed
— was waited out for an hour by the bench's deadline; both drivers now give
up after ~3 min of dead console and the wrappers power-cycle and re-run
**that run only**.

## The value of an effort level, in two views (2026-09-15)

`analysis/value_of_effort.py` (→ `value_of_effort.json/.png`) composes the
stage table with the measured corpus retention for all 27 J2K combinations.
Classification always does the full 99 tiles; segmentation and the encoder
only see what the stages before them retained, so a rung's per-frame cost
depends on the upstream rungs. Two views, because that dependence cuts both
ways:

- **normal** — seg × P(cls kept), enc × P(reached encoder), with the measured
  retention of the actual upstream rungs (what a frame costs in flight);
- **retention-matched** — every rung of a stage charged for the same workload,
  the tiles a perfect upstream would pass: 11.5 % of corpus tiles are ≥60 %
  water (segmentation's oracle load), 7.1 % are water and majority-clear (the
  encoder's). The price list with upstream errors held out.

Outcome = genuinely water-and-clear tiles delivered per 99-tile frame
(WorldCover ≥60 % water, expert mask ≤10 % cloud), encoder L2:

| cls / seg | normal µWh | matched µWh | delivered | precision |
|---|---:|---:|---:|---:|
| low / low | 478 | 517 | 2.87 | 72 % |
| low / med | 494 | 555 | 3.01 | 74 % |
| low / high | 580 | 784 | 3.01 | 74 % |
| med / low | 729 | 691 | 3.65 | 37 % |
| **med / med** | **789** | **730** | **3.84** | 42 % |
| med / high | 1,181 | 958 | 3.89 | 41 % |
| high / low | 1,174 | 1,147 | 3.70 | 39 % |
| high / med | 1,219 | 1,185 | 3.84 | 43 % |
| high / high | 1,517 | 1,414 | 3.79 | 42 % |

Pareto front (energy vs delivered), identical in both views: low/low →
low/med → med/low → med/med → med/high. **Classification high is dominated
in every combination**: it delivers no more water-and-clear tiles than the
medium rung (3.70–3.84 vs 3.65–3.89) at 1.5–1.6× the frame energy, because
its extra EuroSAT accuracy (98.5 vs 92.1 %) does not translate into finding
more water on CloudSEN12 — the medium rung already finds 96 % of it (Phase 1
gate: recall 78 / 96 / 93 %). The low classifier is *conservative*: it keeps
4.3 % of tiles at 72 % precision (the corpus has 4.2 % water-and-clear) and
misses a quarter of the water, which is why it delivers 2.9 tiles to the
medium rung's 3.8 — and why every downstream stage looks cheap behind it in
the normal view (segmentation-high costs +86 µWh behind cls-low, +392 behind
cls-med). The matched view removes that: seg-high is +229 µWh over seg-med at
the oracle load regardless of the classifier. The encoder rung buys
robustness, not delivery: +5–14 µWh per frame per layer step (normal) for
+3.5 / +1.8 dB one-frame-lost PSNR.

**Swapped order, measured** (`analysis/pipeline_retention_swapped.py`,
segmentation → classification, classifier fed the module's cloud-filled
tile): the delivered sets are the same to within one tile (e2e retention
4.0–4.1 / 9.0–9.8 / 9.1–9.5 % per classifier, precision 72–74 / 38–43 / 39–42
%) and segmentation now runs on all 99 tiles instead of 4–20 % of them, so the
swap costs 163 / 496 / 2,486 µWh per frame more at the three segmentation
rungs for no change in outcome. The deployed order stands. The reason the
swap does not help: the classifiers keep majority-cloud tiles as water
(medium: 222 of 419 retained tiles), but segmentation removes those either
way; what the swap cannot remove are the classifier's clear-land false
positives (46 / 40 tiles for med / high), which are the precision gap.

## Why demosaic takes 690 ms (2026-09-17)

Measured on the board, same frame, same session, n=20, bracketed floors
(`demosaic_modes/`): `radiometric_mode 1` (the frozen gain rendering)
**683 ms**, `radiometric_mode 0` (the old `NORM_MINMAX`) **364 ms** — the
Stage 4 figure. The difference is the rendering path in
`demosaic_module.cpp`: `convertTo(CV_32FC3)` turns the 30 MB 16-bit BGR image
into 60 MB of float, then `multiply`, `add`, `min`, `max` run as four separate
full passes before a `convertTo(CV_16UC3)` back — six passes over 60 MB on a
Cortex-A53 at ~2–3 GB/s effective bandwidth, +320 ms. Energy: demosaic + tile
+ I/O 533 vs 439 µWh/frame, so the rendering is ~94 µWh of the 216 µWh fixed
stages. The 180° rotation is also done with `warpAffine` (a bilinear
resample) where `cv::rotate` is a flip, and the module emits 16-bit output
(30 MB) of which the tile stage uses the low byte.

**Fixed the same day** (`tf-dipp-module` `src/demosaic_module.cpp`,
`src/tile_module.cpp`; run-set `demosaic-onepass`, deployed and md5-verified):
`cv::rotate(ROTATE_180)` for the rotation; for the frozen rendering (equal
gains, zero offsets) one `convertTo(out, CV_8UC3, gain)` — `saturate_cast`
multiplies, rounds to nearest and clips in a single pass; 8-bit output when
`normalization_value` is 255, with the tile stage taking an 8-bit buffer in
place (it hard-assumed 16-bit containers). The per-channel float path stays
as the fallback for non-uniform gains. Measured on the board
(`demosaic_onepass/`):

| | before | after |
|---|---:|---:|
| demosaic EXEC | 683 ms | **241 ms** |
| tile EXEC | 143 ms | **99 ms** (no 16→8 parse pass, half the input) |
| demosaic + tile energy (four baselines, − I/O) | 216 µWh | **57 ± 20 µWh** |
| pipelines 2 / 6 / 3, matched frame, n=20 | 1,099 / 1,747 / 4,172 µWh · 5.2 / 7.7 / 12.5 s | **952 / 1,571 / 3,952 µWh · 4.7 / 7.1 / 11.7 s** |

**Pixel-exact against the host** (`analysis/board_tiles_exact.py`): the
board's per-tile JPEG XL sizes at fixed distance equal the host's for **99 of
99 tiles** when the host renders `rotate(cvtColor(raw)) · 0.050` and rounds
to nearest — and for 2 of 99 when it truncates. Two things that settles: the
old `warpAffine` rotation (`getRotationMatrix2D(centre, 180, 1)`) mapped
source column 0 off the image and left destination column 0 and row 0 black,
a one-pixel shift the host replica never had — on the matched frame the
classifiers now keep 99 / 99 / 96 tiles where they kept 97 / 98 / 97; and the
board rounds after the gain (the old float path did too) while
`train_landcover_ms.py`, `cs12_validate.py` and the frame generator truncate
(`astype(uint8)`). Half an LSB, below any effect measured, but the replicas
should use `np.rint` to be exact; noted for the next retrain rather than
re-scored now. Slots 1 / 2 carry 41 / 17 µWh and 241 / 99 ms.

## Two decisions for the paper (2026-09-17): value per stage, experiments retention-matched

**1. The value of an effort level is measured per stage, on the corpus each
stage was trained for.** The rungs were not trained on the joint
"deliver water-and-clear tiles" task and it shows — on that outcome the high
classification rung finds no more water than the medium one — so the joint
outcome is the *viability* gate (`analysis/e2e_viability.py`, formerly the
value script) and the *value* of a step is what it buys on the stage's own
test quality per unit of cost (`analysis/value_of_effort.py`, cost per
execution on 99 tiles, retention-matched):

| stage | rung | quality (test) | Δquality | µWh / 99 tiles | Δ µWh | Δquality per mWh |
|---|---|---:|---:|---:|---:|---:|
| classification, EuroSAT-MS top-1 | low → med → high | 75.13 → 92.09 → 98.52 % | +16.96, +6.43 | 219 → 394 → 849 | +174, +455 | 97, 14 pp/mWh |
| segmentation, CloudSEN12 IoU | low → med → high | 0.607 → 0.775 → 0.807 | +0.168, +0.032 | 163 → 496 → 2,486 | +333, +1,990 | 0.51, 0.02 IoU/mWh |
| encoder, J2K PSNR one frame lost | L1 → L2 → L3 | 32.51 → 36.04 → 37.88 dB | +3.53, +1.84 | 611 → 741 → 894 | +131, +153 | 27, 12 dB/mWh |

Every stage is concave: the first step is the cheap one, the top step costs
2.2–7× more per unit of quality. Static pipelines at the matched (all 99
tiles) workload, composed with the one-pass fixed stages: 1,050 / 1,688 /
4,286 µWh and 4.8 / 7.1 / 12.1 s per frame; at the oracle workload (11.5 % of
tiles to segmentation, 7.1 % to the encoder) 339 / 560 / 1,255 µWh.

**2. Experiments run retention-matched.** On hardware the only way an effort
level can differ from the next in compute alone is for every stage to see the
same tiles at every effort — and to *discard* the same tiles, so the matched
frames must exercise every exit of the pipeline, not just the path that
reaches the encoder. `training/make_cs12_frames.py --select agreement` keeps
only crops on which **all six rungs take the same decision**, in three
classes: kept by all three classifiers and all three segmenters (reaches the
encoder), kept by all classifiers and discarded by all segmenters (cloudy
water, stops at segmentation), rejected by all classifiers (stops there). On
the test corpus: **89 / 7 / 2,907** of 3,900 crops (897 disagree somewhere).
Those proportions are the corpus's, and CloudSEN12 samples *land* — so the
mix is set from the mission, not the corpus: **Earth-average surface, 71 %
ocean with ~⅔ cloud cover → 23 clear-water / 48 cloudy-water / 28 land tiles
per 99-tile frame** (`--mix 23:48:28`, decided 2026-09-17). The cloudy class
cycles its 7 tiles; harmless for cost, since a segmenter's work does not
depend on which cloud it looks at. Frames `cs12_test_matched_NN` (this mix)
and `cs12_test_matchedkeep_NN` (all 99 reach the encoder, for per-execution
cost hints) are in `~/dipp-datasets/cs12-frames/`.

**Earth-average frame on hardware** (`e2e_matched_mix.json`, tag `mix2`,
one-pass modules, n=20, floors 2.71–2.73 W): classification keeps **71 / 70
/ 69** and **23 / 23 / 22** reach the encoder at low / medium / high —
matched to 98 % of tiles. The first build of the set, selected through the
host's *truncating* rendering, gave 70 / 71 / 67 and 27 / 23 / 22: the
selection has to be made in the board's rendering (round to nearest, now
`make_cs12_frames.render`); the residual 1–2 tiles are the NPU's integer
arithmetic against the host's at the decision boundary. Per frame **519 /
968 / 2,744 µWh and 2.14 / 3.40 / 6.48 s — span 5.3× energy, 3.0×
latency.** This is the operating point the Stage 5 experiments run at.

**Keep-all frame on hardware** (`e2e_matched.json`, `cs12_test_matchedkeep_00`, n=20,
bracketed floors 2.71–2.73 W): classification keeps 97 / 98 / 97 of 99 at
low / medium / high and segmentation keeps all of those — the 1–3 tile
difference from the host's 99 is the board's real demosaic at decision
boundaries. Direct per frame **1,099 / 1,747 / 4,172 µWh, 5.2 / 7.7 / 12.5 s** with
the old demosaic; **952 / 1,571 / 3,952 µWh, 4.7 / 7.1 / 11.7 s — span 4.2×
energy, 2.5× latency — with the one-pass modules** (§ *Why demosaic takes
690 ms*); the composition reproduces the latency to <1 % and the energy to
8–10 %.

**The encoder's cost is content-dependent, and the matched frame is its
honest workload.** Per-module medians on the matched frame match the benches
for the models (cls 557 / 915 / 1,846 ms, seg 592 / 1,461 / 4,104) but J2K
ran 32 % faster than on the random frame, and re-measured there
(`energy_enc_matched/`, n=20, bracketed): **L1 / L2 / L3 3,363 / 4,511 /
5,843 ms and 611 / 741 / 894 µWh per 99 tiles**, bytes 3,024–3,042 — rate
allocation converges faster on smooth open water, which is all the encoder
ever sees in flight. JXL's time is content-independent (e3 / e5 2,537 /
18,764 ms, 607 / 2,770 µWh) but at a fixed *distance* its bytes collapse on
that content: **536 / 668 B per tile against the 2,988 / 3,014 it emits on
the corpus mean**. The frozen-3 kB comparison therefore holds for J2K by
construction (fixed ratio) and for JXL only at corpus-mean content; on
matched frames JXL sends a fifth of the bytes for the same energy, which is
a point in its favour on the link and changes nothing on the board. Slots
11 / 12 / 16 / 17 / 18 carry the matched-frame energies.
