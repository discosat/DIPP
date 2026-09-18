#!/usr/bin/env python3
"""Score end-to-end board runs on CloudSEN12+ frames against their labels.

    analysis/e2e_score.py analysis/stage4.5-models/e2e_runs.json

For each (pipeline, frame) run recorded by tools/phase4_e2e.py:
  * classification -- board decision per tile (tiler order) vs WorldCover:
    water recall / precision on tiles that are >=60% water and <=10% cloud
    by the expert mask (the same gate as analysis/cs12_classifier_validate.py,
    now measured on hardware through the real demosaic);
  * segmentation -- for every retained tile, the board's clear fraction vs the
    expert mask's; retention decision vs 'truly majority clear';
  * end to end -- tiles uploaded per frame and the share of them that are
    genuinely water-and-clear (the mission outcome);
  * composed latency per frame from the EXEC lines.
The frame labels come from ~/dipp-datasets/cs12-frames/<frame>.json.
"""
import json, os, sys
import numpy as np

FRAMES = os.path.expanduser("~/dipp-datasets/cs12-frames")
WATER = 9


def main():
    runs = json.load(open(sys.argv[1]))
    rows = []
    for key, r in runs.items():
        stem = r["image"].replace(".bayerRG", "")
        meta = json.load(open(os.path.join(FRAMES, stem + ".json")))
        tiles = meta["tiles"]
        preds = r.get("cls_preds") or []
        if len(preds) < 99:
            print(f"{key}: no ordered predictions ({len(preds)}); skipped"); continue
        preds = preds[:99]
        truth_water = [t.get("lc", {}).get("water", 0) >= 0.6 for t in tiles]
        visible = [t["thick"] + t["thin"] <= 0.10 for t in tiles]
        clear_maj = [t["thick"] + t["thin"] < 0.5 for t in tiles]
        kept_idx = [k for k, p in enumerate(preds) if p == WATER]
        tp = sum(1 for k in kept_idx if truth_water[k] and visible[k])
        n_water = sum(1 for k in range(99) if truth_water[k] and visible[k])
        n_kept_vis = sum(1 for k in kept_idx if visible[k])
        rec = tp / n_water if n_water else float("nan")
        prec = tp / n_kept_vis if n_kept_vis else float("nan")
        # segmentation on the retained tiles, in order
        seg = r.get("seg_kept") or []
        seg = seg[:len(kept_idx)]
        seg_rows = []
        for (a, b), k in zip(seg, kept_idx):
            board_clear = a / b
            seg_rows.append((board_clear, tiles[k]["clear"] + tiles[k]["shadow"], board_clear > 0.5, clear_maj[k]))
        agree = np.mean([x[2] == x[3] for x in seg_rows]) if seg_rows else float("nan")
        mae = np.mean([abs(x[0] - x[1]) for x in seg_rows]) if seg_rows else float("nan")
        uploaded = [k for (a, b), k in zip(seg, kept_idx) if a > 0.5 * b]
        value = np.mean([truth_water[k] and visible[k] for k in uploaded]) if uploaded else float("nan")
        lat = {}
        for m, us in r["execs"]:
            lat.setdefault(m, []).append(us)
        composed = sum(sorted(v)[len(v) // 2] for v in lat.values()) / 1000
        rows.append(dict(run=key, pipeline=r["pipeline"], frame=stem, n_water_visible=n_water,
                         cls_kept=len(kept_idx), water_recall=rec, water_precision=prec,
                         seg_n=len(seg_rows), seg_decision_agreement=float(agree), seg_clear_mae=float(mae),
                         uploaded=len(uploaded), uploaded_reported=r["uploaded"], value=float(value),
                         composed_ms=composed))
        print(f"{key:32} water tiles {n_water:2}  cls kept {len(kept_idx):2}  recall {100*rec:5.1f}%  prec {100*prec:5.1f}%"
              f" | seg n={len(seg_rows):2} decision agree {100*agree:5.1f}%  clear-frac MAE {mae:.3f}"
              f" | uploaded {len(uploaded):2} water&clear {100*value:5.1f}%  | {composed:8.0f} ms/frame")
    out = sys.argv[1].replace(".json", "_scored.json")
    json.dump(rows, open(out, "w"), indent=1)
    print(f"wrote {out}")


if __name__ == "__main__":
    sys.exit(main())
