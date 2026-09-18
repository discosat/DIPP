# CloudSEN12+ — facts that affect our training, from the upstream notebooks

Source: the CloudSEN12 TACO demo, prediction and training notebooks
(cloudsen12.github.io). Recorded here because each of these is a silent
correctness trap rather than a documentation nicety.

## Images are PADDED, and the padding is not centred

> "The images are padded from 509x509 to 512x512 ... The padding is filled with
> zeros in the **left and bottom** sides of the image."

⚠️ **The documented side is wrong.** Measured on the fetched corpus, the valid
window is `rows 0:509 cols 0:509` -- the zeros are on the **right and bottom**.
Hardcoding the documented left-side crop would have kept the zero stripe on
every sample *and* thrown away three columns of real data, with nothing in the
output to show for it. The loader finds the window empirically for this reason;
that is not defensive over-engineering, it is the only thing that caught this.

Our fetch reads `Window(0, 0, 512, 512)`, so **every stored sample carries a
3-pixel zero stripe**. This matters twice over:

- A crop that lands on the stripe trains the model on black bars.
- Worse, those pixels carry a *label*, so the model is taught that black means
  whatever the padding is labelled -- most likely "clear". A cloud detector that
  has learned "dark = clear" is exactly the failure we already have on device.

The loader therefore finds the valid region empirically (rows/columns that are
not identically zero across a sample) rather than trusting the documented side.
Trusting it would be one transposition away from silently cropping the wrong
edge.

## Scaling

Values are Sentinel-2 TOA reflectance stored as uint16. The upstream models
normalise with `/10_000`; the demo *displays* RGB with `/3000`, so typical
bright ground sits near 3000 and cloud runs toward 10000. That range is the
anchor for the reflectance -> device-uint8 affine: the device domain is
mean 94.4/104.5/111.2, std 50.4/34.8/29.6 per band (pooled over 100 real
calibrated tiles from four scenes).

## Bands

RGB is `read([4, 3, 2])` = B4/B3/B2. Our fetch already uses this.
The full cube is 13 bands; the upstream UNetMobV2 models take all 13
(`in_channels=13`). **We take 3**, because the payload's camera delivers RGB
after demosaic -- a 13-band model cannot be deployed on this hardware, so the
upstream accuracy figures are not a target we can compare against.

## Labels

Four classes: `0 clear, 1 thick cloud, 2 thin cloud, 3 cloud shadow`
(`smp.Unet(..., classes=4)` upstream).

`tflite_binary_segmentation` has a single sigmoid output, so these must be
collapsed. The collapse is a modelling decision with real consequences:

- thick only -> reproduces the 38-Cloud problem, where detection is close to a
  brightness threshold and model capacity has nothing to buy
- thick + thin -> the version that gives the effort ladder something to earn
- shadow as cloud -> defensible (shadowed ground is also unusable) but changes
  what "retained" means for the downlink argument

## Split selection

`label_type == "high"` and `real_proj_shape == 509`, split on
`tortilla:data_split` into train/validation/test. Our fetch already filters this
way, so the upstream splits are preserved and val/test stay honest.

## Tooling version drift

`tacoreader` 2.0 removed the v1 `tacofoundation:` format; the upstream notebooks
were written against 0.5.2. `fetch_cloudsen12.py` imports `tacoreader.v1` with a
fallback. The HTTP backend also needs `requests` and `aiohttp`, which are not
hard dependencies and fail only at first use.

## ⚠️ The input-quantisation contract (cost: one full board round-trip)

`tflite_binary_segmentation` writes the **raw uint8 tile straight into the input
tensor**. The interpreter then dequantises it as `(v - zp) * scale`. So the
model only receives the values it was trained on if

    scale = 2/255 = 0.007843   and   zp = 128

because that is the only pair for which `(v - zp) * scale == v/127.5 - 1`. Every
working model has it. The training script alludes to this -- "normalisation
lives in the input tensor's quantisation parameters, so the module still passes
raw uint8 through unchanged" -- but does not say it is load-bearing.

**How it broke.** Calibrating PTQ on real device tiles seemed obviously right:
they are the deployment distribution. But the board's calibration cannot emit
brighter than 182/165/164, so those tiles span only `[-0.87, 0.43]`, and the
converter derived `scale=0.005075, zp=171`. Under passthrough a pixel of 182
then arrives as **0.056 instead of 0.427** -- the whole range squeezed into a
narrow band, nothing crossing a 0.5 threshold.

    host   96.58% masked on koge-coast-full-clouds
    board   0.00% masked, NPU and CPU alike

**A tighter, better-fitted range is fatal here**, which is the opposite of the
usual quantisation intuition and why it was not obvious.

**How it was identified rather than guessed.** NPU and CPU gave *identical*
0.00%. A delegate fault would differ between them; only something upstream of
both -- the numbers entering the graph -- can be bit-identical on two different
processors. That single comparison separated "hardware is wrong" from "input is
wrong" without any further instrumentation.

**The fix**: `representative_device()` yields one synthetic full-range ramp
before the real tiles, pinning the endpoints to [-1, 1] while the remaining
samples keep interior activation statistics honest. Re-export gives
`q=(0.007843, 127)`.

**The better fix, not taken yet**: the module should apply the model's declared
quantisation instead of assuming a specific one --
`q = round((v/127.5 - 1)/scale + zp)`, which reduces to identity when
`scale=2/255, zp=128` and so is backward compatible with every existing model.
That removes a silent coupling between the converter and the C++ module, where
today a converter that picks a legitimate quantisation produces a model that
loads, runs, and returns nothing. Worth doing before the campaigns; it needs a
module rebuild and redeploy.

**Do not "fix" this by lowering pixel_threshold.** The threshold would appear to
work while every input value remained wrong.
