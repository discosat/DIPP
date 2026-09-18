#!/usr/bin/env python3
"""End-to-end VIABILITY on CloudSEN12+: mission outcome per frame against cost
for every effort combination -- the gate that the pipeline composes on
independent imagery, in two cost views. NOT the value-of-effort figure: the
rungs were not trained on the joint task and it shows (classification-high
finds no more water than medium). The value of an effort level is measured
per stage on each stage's own test corpus by analysis/value_of_effort.py.

    analysis/e2e_viability.py

Outcome side (CloudSEN12+ validation, 2,140 tiles through the exact module
chain -- analysis/pipeline_retention.py): for each classification x
segmentation pair, the fraction of tiles that reach the encoder and the share
of those that are genuinely water-dominated (WorldCover >= 60%) and cloud-free
(expert mask <= 10%). The encoder rung does not change which tiles are sent;
it sets what survives a lost CCSDS frame (J2K one-frame-lost PSNR on the
corpus). Cost side: per-stage energies from analysis/energy_cal_summary.py
(settled-floor calibration, I/O term removed) and latencies from the same runs.

Classification always does the same work -- all 99 tiles. Segmentation and the
encoder only see what the stages before them retained, so their per-frame cost
depends on the upstream rungs. Two views, because that dependence cuts both
ways:

  NORMAL      seg x P(cls kept), enc x P(reached encoder), with the measured
              retention of the actual upstream rungs. This is what a frame
              costs in flight; a conservative low classifier makes every
              downstream rung look cheap.
  MATCHED     every rung of a stage charged for the SAME workload: the tiles a
              perfect upstream would pass (water >= 60% by WorldCover for
              segmentation, water and majority-clear for the encoder -- 11.5%
              and 7.1% of the corpus). This is the price list: what the rung
              itself costs, upstream errors held out.
"""
import itertools, json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
S = os.path.join(HERE, "stage4.5-models")
R = json.load(open(os.path.join(S, "pipeline_retention.json")))
E = json.load(open(os.path.join(S, "stage_energy.json")))
ENCJ = json.load(open(os.path.join(S, "encoder_energy.json")))     # analysis/energy_cal_summary.py --enc
# oracle workloads (share of corpus tiles), ~/dipp-datasets/cs12-frames validation labels
R_SEG, R_ENC = 0.115, 0.0707
# latency, ms per 99-tile frame (module EXEC medians; fixed stages from the e2e runs)
MS = E["_exec_ms"]
FIXED = dict(ms=241 + 99, uwh=E["fixed"]["uwh"])
CLS = {"low": (MS["cls_low"], E["cls_low"]["uwh"]), "med": (MS["cls_medop"], E["cls_med"]["uwh"]),
       "high": (MS["cls_high"], E["cls_high"]["uwh"])}
SEG = {"low": (MS["seg_low_keepall"], E["seg_low"]["uwh"]), "med": (MS["seg_med_keepall"], E["seg_med"]["uwh"]),
       "high": (MS["seg_high_keepall"], E["seg_high"]["uwh"])}
ENC = {k: (v["ms"], v["uwh"], v["psnr_1lost"]) for k, v in ENCJ.items() if k.startswith("L")}   # J2K is the flight encoder
CLS_RET = R["cls"]
COL = {"low": "#1baf7a", "med": "#eda100", "high": "#eb6834"}      # fixed categorical slots
MK = {"low": "o", "med": "s", "high": "^"}


def compose(c, s, e, view):
    r = R["e2e"][f"{c}/{s}"]
    p_seg = CLS_RET[c] if view == "normal" else R_SEG
    p_enc = r["e2e_ret"] if view == "normal" else R_ENC
    ms = FIXED["ms"] + CLS[c][0] + SEG[s][0] * p_seg + ENC[e][0] * p_enc
    uwh = FIXED["uwh"] + CLS[c][1] + SEG[s][1] * p_seg + ENC[e][1] * p_enc
    return dict(cls=c, seg=s, enc=e, view=view, ms=ms, uwh=uwh, work_uwh=uwh - FIXED["uwh"],
                sent_per_frame=99 * r["e2e_ret"], delivered=99 * r["e2e_ret"] * r["value"],
                precision=r["value"], degraded_psnr=ENC[e][2],
                value_per_mwh=99 * r["e2e_ret"] * r["value"] / (uwh / 1000))


def main():
    out = dict(fixed=FIXED, cls=CLS, seg=SEG, enc=ENC, oracle=dict(seg=R_SEG, enc=R_ENC), views={})
    for view in ("normal", "matched"):
        rows = [compose(c, s, e, view) for c, s, e in itertools.product(CLS, SEG, ENC)]
        rows.sort(key=lambda x: x["uwh"])
        print(f"\n== {view.upper()} view" + (" (measured upstream retention)" if view == "normal"
              else f" (seg sees {100*R_SEG:.1f}%, enc {100*R_ENC:.1f}% of tiles for every rung)"))
        print(f"{'cls':5} {'seg':5} {'enc':3} {'ms/frame':>9} {'uWh/frame':>10} {'sent':>5} {'delivered':>9} {'prec':>5} {'PSNR@1lost':>10} {'deliv/mWh':>9}")
        for x in rows:
            print(f"{x['cls']:5} {x['seg']:5} {x['enc']:3} {x['ms']:9.0f} {x['uwh']:10.0f} {x['sent_per_frame']:5.1f} "
                  f"{x['delivered']:9.2f} {100*x['precision']:4.0f}% {x['degraded_psnr']:10.2f} {x['value_per_mwh']:9.2f}")
        front, best = [], -1
        for x in rows:
            if x["delivered"] > best + 1e-9:
                front.append(x); best = x["delivered"]
        print(f"Pareto front (energy vs delivered water-and-clear tiles per frame):")
        for x in front:
            print(f"  {x['cls']}/{x['seg']}/{x['enc']}: {x['uwh']:.0f} uWh -> {x['delivered']:.2f} tiles "
                  f"({100*x['precision']:.0f}% precise), {x['ms']/1000:.2f} s")
        out["views"][view] = dict(rows=rows, front=front)
    # the three static pipelines, both views, for the report
    print("\nstatic pipelines (low/low/L1, med/med/L2, high/high/L3):")
    for view in ("normal", "matched"):
        for c, e in (("low", "L1"), ("med", "L2"), ("high", "L3")):
            x = compose(c, c, e, view)
            print(f"  {view:8} {c:5} {x['uwh']:7.0f} uWh  {x['ms']:6.0f} ms  delivered {x['delivered']:.2f}")
    p = os.path.join(S, "e2e_viability.json")
    json.dump(out, open(p, "w"), indent=1)
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.4))
        for a, view in zip(ax, ("normal", "matched")):
            for x in out["views"][view]["rows"]:
                if x["enc"] != "L2":
                    continue
                a.scatter(x["uwh"] / 1000, x["delivered"], c=COL[x["cls"]], marker=MK[x["seg"]], s=48,
                          edgecolors="white", linewidths=0.8)
                a.annotate(f"{x['cls'][0]}/{x['seg'][0]}", (x["uwh"] / 1000, x["delivered"]), fontsize=7,
                           xytext=(4, 3), textcoords="offset points", color="#52514e")
            a.set_xlabel("energy per frame, mWh (encoder L2)"); a.set_ylabel("water & clear tiles delivered per frame")
            a.set_title("measured retention" if view == "normal" else "retention-matched (oracle workload)", fontsize=10)
            a.grid(True, color="#e6e5e1", linewidth=0.6); a.set_axisbelow(True)
            for sp in ("top", "right"):
                a.spines[sp].set_visible(False)
        from matplotlib.lines import Line2D
        h = [Line2D([], [], color=COL[k], marker="o", ls="", label=f"classification {k}") for k in COL] + \
            [Line2D([], [], color="#52514e", marker=MK[k], ls="", label=f"segmentation {k}") for k in MK]
        ax[1].legend(handles=h, fontsize=7, frameon=False, loc="lower right")
        fig.tight_layout(); fig.savefig(p.replace(".json", ".png"), dpi=140)
        print(f"wrote {p} and .png")
    except Exception as ex:
        print(f"wrote {p} (no plot: {ex})")


if __name__ == "__main__":
    sys.exit(main())
