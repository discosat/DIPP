#!/usr/bin/env python3
"""The value of an effort level, per stage: quality on the stage's OWN test
corpus against the measured cost of one execution, retention-matched.

    analysis/value_of_effort.py          # -> stage4.5-models/value_of_effort.{json,png}

Why per stage. The rungs were trained for their own tasks (EuroSAT-MS land
cover, CloudSEN12 cloud masks) and not for the joint "deliver water-and-clear
tiles" task, and it shows: on that joint outcome the high classification rung
finds no more water than the medium one (analysis/e2e_viability.py). The joint
outcome is the viability gate; the VALUE of an effort step is what the step
buys on the quality the stage was built for, per unit of cost.

Why retention-matched. Cost is per execution on a full 99-tile frame -- the
work the scheduler commits to when it picks the slot -- so every rung of a
stage is charged for the same workload and an effort level differs from the
next only in compute. In flight, segmentation and the encoder see only what
upstream retained; the matched cost at the oracle workload (tiles a perfect
upstream would pass: 11.5% of corpus tiles >= 60% water for segmentation, 7.1%
water and majority-clear for the encoder) is reported alongside. Board
experiments use the matched frame set (training/make_cs12_frames.py --select
agreement) so the same holds on hardware.

Quality: EuroSAT-MS testing top-1 (5,400 images); CloudSEN12 test IoU (975
expert-labelled scenes, cs12_test_g050.json); one-512-B-frame-lost PSNR on the
300-tile encoder corpus (encoder_energy.json / encoder_corpus.json). Cost:
stage_energy.json (per-run idle floors, I/O term removed), encoder_energy.json.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
S = os.path.join(HERE, "stage4.5-models")
E = json.load(open(os.path.join(S, "stage_energy.json")))
_mp = os.path.join(S, "encoder_energy_matched.json")          # encoder on the matched frame (flight content)
ENC = json.load(open(_mp if os.path.exists(_mp) else os.path.join(S, "encoder_energy.json")))
ENC_RANDOM = json.load(open(os.path.join(S, "encoder_energy.json")))
for _k in ("L1", "L2", "L3"):                                  # quality is the corpus's either way
    ENC[_k]["psnr_1lost"] = ENC_RANDOM[_k]["psnr_1lost"]
CLSQ = {r["model"]: r["top1"] for r in json.load(open(os.path.join(S, "phase1_cls_holdout.json")))}
SEGQ = {r["tag"]: r for r in json.load(open(os.path.join(S, "cs12_test_g050.json")))}
MS = E["_exec_ms"]
R_SEG, R_ENC = 0.115, 0.0707
FIXED = dict(ms=241 + 99, uwh=E["fixed"]["uwh"])      # one-pass demosaic + 8-bit tile, matched-frame medians (2026-09-17)

STAGES = {
    "classification (EuroSAT-MS test top-1, %)": [
        ("low", "custom_conv @224", 100 * CLSQ["landcover_ms_low_qat.tflite"], MS["cls_low"], E["cls_low"]["uwh"], 1.0),
        ("med", "MNv2 a0.5 @224, op. point", 100 * CLSQ["landcover_ms_medop_qat.tflite"], MS["cls_medop"], E["cls_med"]["uwh"], 1.0),
        ("high", "MNv2 a1.4 @224", 100 * CLSQ["landcover_ms_high_qat.tflite"], MS["cls_high"], E["cls_high"]["uwh"], 1.0),
    ],
    "segmentation (CloudSEN12 test IoU)": [
        ("low", "plain U-Net w8d3 @96", SEGQ["cs12g050_p_w08d3_r096"]["iou"], MS["seg_low_keepall"], E["seg_low"]["uwh"], R_SEG),
        ("med", "MNv2 a0.35 @160, sel. QAT", SEGQ["cs12g050q_g_a035_r160"]["iou"], MS["seg_med_keepall"], E["seg_med"]["uwh"], R_SEG),
        ("high", "MNv2 a1.4 @224, sel. QAT", SEGQ["cs12g050q_g_a14_r224"]["iou"], MS["seg_high_keepall"], E["seg_high"]["uwh"], R_SEG),
    ],
    "encoder (J2K, PSNR with one frame lost, dB)": [
        ("L1", "1 quality layer", ENC["L1"]["psnr_1lost"], ENC["L1"]["ms"], ENC["L1"]["uwh"], R_ENC),
        ("L2", "2 layers", ENC["L2"]["psnr_1lost"], ENC["L2"]["ms"], ENC["L2"]["uwh"], R_ENC),
        ("L3", "3 layers", ENC["L3"]["psnr_1lost"], ENC["L3"]["ms"], ENC["L3"]["uwh"], R_ENC),
    ],
}
COL = {"low": "#1baf7a", "med": "#eda100", "high": "#eb6834", "L1": "#1baf7a", "L2": "#eda100", "L3": "#eb6834"}


def main():
    out = {"fixed": FIXED, "oracle": dict(seg=R_SEG, enc=R_ENC), "stages": {}}
    for stage, rungs in STAGES.items():
        print(f"\n== {stage} -- cost per execution on 99 tiles; 'matched' = at the oracle workload")
        print(f"{'rung':5} {'model':28} {'quality':>8} {'dq':>7} {'ms':>7} {'uWh':>7} {'d_uWh':>7} {'dq/mWh':>8} {'matched uWh':>12}")
        rows, prev = [], None
        for name, model, q, ms, uwh, share in rungs:
            dq = duwh = dms = val = None
            if prev:
                dq, dms, duwh = q - prev["quality"], ms - prev["ms"], uwh - prev["uwh"]
                val = dq / (duwh / 1000) if duwh else float("inf")
            row = dict(rung=name, model=model, quality=q, ms=ms, uwh=uwh, matched_uwh=uwh * share,
                       matched_ms=ms * share, dq=dq, dms=dms, duwh=duwh, dq_per_mwh=val)
            rows.append(row); prev = row
            f = lambda v, w: f"{v:{w}.2f}" if v is not None else " " * w
            print(f"{name:5} {model:28} {q:8.2f} {f(dq, 7)} {ms:7.0f} {uwh:7.0f} {f(duwh, 7)} {f(val, 8)} {uwh*share:12.1f}")
        span_q = rows[-1]["quality"] - rows[0]["quality"]
        print(f"      span: quality +{span_q:.2f}, latency x{rows[-1]['ms']/rows[0]['ms']:.2f}, energy x{rows[-1]['uwh']/rows[0]['uwh']:.2f}")
        out["stages"][stage] = rows
    # pipeline totals, matched: all-99-tiles (the matched frame set) and oracle workload
    cls, seg, enc = (out["stages"][k] for k in STAGES)
    print("\n== static pipelines, retention-matched (fixed + cls + seg + enc), uWh per frame")
    print(f"{'pipeline':10} {'all 99 tiles':>13} {'oracle load':>12} {'ms (all 99)':>12}")
    tot = {}
    for i, name in enumerate(("low", "medium", "high")):
        all99 = FIXED["uwh"] + cls[i]["uwh"] + seg[i]["uwh"] + enc[i]["uwh"]
        orc = FIXED["uwh"] + cls[i]["uwh"] + seg[i]["matched_uwh"] + enc[i]["matched_uwh"]
        ms = FIXED["ms"] + cls[i]["ms"] + seg[i]["ms"] + enc[i]["ms"]
        tot[name] = dict(all99_uwh=all99, oracle_uwh=orc, all99_ms=ms)
        print(f"{name:10} {all99:13.0f} {orc:12.0f} {ms:12.0f}")
    print(f"span high/low: x{tot['high']['all99_uwh']/tot['low']['all99_uwh']:.2f} energy (all 99), "
          f"x{tot['high']['oracle_uwh']/tot['low']['oracle_uwh']:.2f} (oracle load), x{tot['high']['all99_ms']/tot['low']['all99_ms']:.2f} latency")
    out["pipelines"] = tot
    p = os.path.join(S, "value_of_effort.json")
    json.dump(out, open(p, "w"), indent=1)
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))
        for ax, (stage, rows) in zip(axes, out["stages"].items()):
            xs = [r["uwh"] / 1000 for r in rows]; ys = [r["quality"] for r in rows]
            ax.plot(xs, ys, color="#9a9890", lw=1.2, zorder=1)
            for r, x, y in zip(rows, xs, ys):
                ax.scatter(x, y, s=60, color=COL[r["rung"]], edgecolors="white", linewidths=0.8, zorder=2)
                ax.annotate(r["rung"], (x, y), fontsize=8, xytext=(5, -3), textcoords="offset points", color="#52514e")
            for a_, b_ in zip(rows, rows[1:]):
                ax.annotate(f"+{b_['dq']:.2f} / +{b_['duwh']:.0f} µWh", ((a_["uwh"] + b_["uwh"]) / 2000, (a_["quality"] + b_["quality"]) / 2),
                            fontsize=7, color="#52514e", ha="left", xytext=(4, 4), textcoords="offset points")
            ax.set_title(stage, fontsize=9); ax.set_xlabel("energy per execution, mWh (99 tiles)")
            ax.grid(True, color="#e6e5e1", linewidth=0.6); ax.set_axisbelow(True)
            for sp in ("top", "right"):
                ax.spines[sp].set_visible(False)
        axes[0].set_ylabel("quality on the stage's own test set")
        fig.tight_layout(); fig.savefig(p.replace(".json", ".png"), dpi=140)
        print(f"\nwrote {p} and .png")
    except Exception as ex:
        print(f"\nwrote {p} (no plot: {ex})")


if __name__ == "__main__":
    sys.exit(main())
