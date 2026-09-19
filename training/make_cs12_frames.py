#!/usr/bin/env python3
"""Build CloudSEN12+ frames in the payload's raw format, so the whole pipeline
-- demosaic, tile, classification, segmentation, encoder -- runs on labelled
Sentinel-2 imagery on the board.

    training/make_cs12_frames.py --split validation --out ~/dipp-datasets/cs12-frames
    training/make_cs12_frames.py --split test       --out ~/dipp-datasets/cs12-frames

Each frame is 2464x2056 Bayer RG in 16-bit little-endian containers, exactly
what sim/camera_control.c feeds the daemon (10,131,968 bytes). It is a mosaic
of 99 CloudSEN12 crops (224 px, 2x2 grid of every 509 px scene, shuffled with
a fixed seed) laid out in the tiler's 11x9 grid, so board tile k IS corpus
crop k of that frame. The bottom 40 rows, which the tiler discards, are a
mirror of the rows above.

Rendering contract. The demosaic module does cvtColor(BayerRG2BGR), rotates
180 degrees, multiplies by the frozen gain (0.050, zero offset) and clips to
255. The raw values written here are the corpus's L1C digital numbers
directly, pre-rotated and mosaicked with the Bayer phase that reproduces the
module's channel order -- found empirically below against the same OpenCV
chain, not assumed from the constant's name. DNs above 4095 are kept (the
container is 16-bit and the module does not clip before the gain), so the
frames saturate exactly where the corpus rendering does. Demosaic
interpolation is the only difference from the corpus tile, and it is the same
difference a real sensor would have.

Per frame, <name>.json carries per-tile provenance and labels: scene id, crop
offset, thick/thin/shadow/clear fractions from the expert mask, WorldCover
group shares (water etc.), and <name>.labels.npz the 224x224 expert mask and
land-cover raster per tile, so every board output can be scored.
"""
import argparse, glob, json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "analysis"))
DATA = os.path.expanduser("~/dipp-datasets/cloudsen12")
H, W, TILE = 2056, 2464, 224
COLS, ROWS = W // TILE, H // TILE            # 11 x 9 = 99
GAIN = 0.050
THICK, THIN, SHADOW = 1, 2, 3


def render(dn):
    """The board's rendering: gain, ROUND to nearest, clip -- convertTo's
    saturate_cast. Truncation (astype) differs by half an LSB and flipped a
    handful of decisions at the boundary when the agreement set was built."""
    return np.clip(np.rint(dn.astype(np.float32) * GAIN), 0, 255).astype(np.uint8)


def mosaic(rgb, phase):
    """Sample one channel per pixel. phase = ((c00, c01), (c10, c11)) channel idx."""
    out = np.empty(rgb.shape[:2], np.uint16)
    for dy in (0, 1):
        for dx in (0, 1):
            out[dy::2, dx::2] = rgb[dy::2, dx::2, phase[dy][dx]]
    return out


def module_chain(raw):
    """Exactly demosaic_module.cpp: cvtColor BayerRG2BGR, rotate 180, gain, clip."""
    import cv2
    dem = cv2.rotate(cv2.cvtColor(raw, cv2.COLOR_BayerRG2BGR), cv2.ROTATE_180)
    return render(dem)


def find_phase(rgb):
    """Which 2x2 phase, after the module's chain, returns channels as R,G,B."""
    import cv2
    best = None
    for phase in [((0, 1), (1, 2)), ((1, 0), (2, 1)), ((1, 2), (0, 1)), ((2, 1), (1, 0))]:
        raw = mosaic(rgb[::-1, ::-1], phase)                 # pre-rotate 180
        out = module_chain(raw)
        ref = render(rgb)
        err = float(np.mean((out[8:-8, 8:-8].astype(float) - ref[8:-8, 8:-8]) ** 2))
        if best is None or err < best[0]:
            best = (err, phase)
    return best[1], best[0]


def crops(split):
    from cs12_classifier_validate import WC_GROUP, GROUPS
    fs = sorted(glob.glob(os.path.join(DATA, split, "*.npz")))
    for f in fs:
        sid = os.path.basename(f)[:-4]
        lcf = os.path.join(DATA, split + "_lc", sid + ".npz")
        with np.load(f) as z:
            rgb = np.transpose(z["rgb"], (1, 2, 0)); lab = z["label"]
        lc = np.load(lcf)["lc"] if os.path.exists(lcf) else None
        for y0 in (0, 509 - TILE):
            for x0 in (0, 509 - TILE):
                t = rgb[y0:y0 + TILE, x0:x0 + TILE]; m = lab[y0:y0 + TILE, x0:x0 + TILE]
                meta = dict(scene=sid, y0=int(y0), x0=int(x0),
                            thick=float((m == THICK).mean()), thin=float((m == THIN).mean()),
                            shadow=float((m == SHADOW).mean()), clear=float((m == 0).mean()))
                g = None
                if lc is not None:
                    g = lc[y0:y0 + TILE, x0:x0 + TILE]
                    cnt = {k: 0 for k in GROUPS}
                    for v in g.ravel():
                        gg = WC_GROUP.get(int(v))
                        if gg: cnt[gg] += 1
                    meta["lc"] = {k: cnt[k] / g.size for k in GROUPS}
                yield t, m, g, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="validation", choices=["validation", "test"])
    ap.add_argument("--out", default=os.path.expanduser("~/dipp-datasets/cs12-frames"))
    ap.add_argument("--seed", type=int, default=43)
    ap.add_argument("--frames", type=int, default=0, help="limit (0 = all full frames)")
    ap.add_argument("--select", default="all", choices=["all", "agreement", "agreement-keep"],
                    help="agreement: only crops on which every deployed rung takes the SAME "
                         "decision -- kept by all three classifiers and all three segmenters "
                         "(reaches the encoder), kept by all classifiers and discarded by all "
                         "segmenters (cloudy water), or rejected by all classifiers -- mixed "
                         "in the proportions they occur in the corpus (--mix), so retention "
                         "per frame is realistic yet identical at every effort. "
                         "agreement-keep: only the first class, every stage does all 99 "
                         "tiles. Frames are filled by cycling a class if it is short.")
    ap.add_argument("--mix", default="23:48:28",
                    help="agreement only: tiles per frame that reach the encoder : stop at "
                         "segmentation : stop at classification. Default is the Earth-average "
                         "surface (71%% ocean, ~2/3 cloud cover -> 23 clear water / 48 cloudy "
                         "water / 28 land), decided 2026-09-17; 'natural' = the corpus's own "
                         "proportions, which are land-heavy (CloudSEN12 samples land: 89 / 7 / "
                         "2907 on test); a bare fraction sets the encoder share alone")
    ap.add_argument("--mix-mode", default="proportional", choices=["proportional", "exact"],
                    help="proportional: each tile slot draws its class from --mix, so frames vary "
                         "around the mean (default); exact: every frame carries the quota")
    ap.add_argument("--name", default="", help="frame name stem (default cs12_<split>[_matched])")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    items = list(crops(a.split))
    rng = np.random.default_rng(a.seed)
    stem = a.name or f"cs12_{a.split}" + {"all": "", "agreement": "_matched", "agreement-keep": "_matchedkeep"}[a.select]
    if a.select.startswith("agreement"):
        sys.path.insert(0, os.path.join(HERE, "..", "analysis"))
        from pipeline_retention import Decider
        decide = Decider()
        cls_ = {"keep": [], "cloudy": [], "reject": []}
        for i, (t, m, g, meta) in enumerate(items):
            d = decide(render(t))
            meta["decisions"] = d
            c, sg = list(d["cls"].values()), list(d["seg"].values())
            if all(c) and all(sg):
                cls_["keep"].append(i)
            elif all(c) and not any(sg):
                cls_["cloudy"].append(i)
            elif not any(c):
                cls_["reject"].append(i)
        n_agree = sum(len(v) for v in cls_.values())
        print(f"agreement classes on {len(items)} crops: keep-all {len(cls_['keep'])}, "
              f"kept by cls / discarded by seg {len(cls_['cloudy'])}, rejected by cls {len(cls_['reject'])} "
              f"({n_agree} agree, {len(items) - n_agree} disagree somewhere)", flush=True)
        if not cls_["keep"]:
            sys.exit("no agreement crops")
        N = COLS * ROWS
        if a.select == "agreement-keep":
            share = {"keep": 1.0, "cloudy": 0.0, "reject": 0.0}
        elif a.mix == "natural":
            share = {k: len(v) / n_agree for k, v in cls_.items()}
        elif ":" in a.mix:                                   # explicit keep:cloudy:reject
            k_, c_, r_ = (float(x) for x in a.mix.split(":")); t = k_ + c_ + r_
            share = {"keep": k_ / t, "cloudy": c_ / t, "reject": r_ / t}
        else:                                                # keep share, rest in corpus proportion
            f = float(a.mix); rest = 1 - f
            cr = len(cls_["cloudy"]) + len(cls_["reject"])
            share = {"keep": f, "cloudy": rest * len(cls_["cloudy"]) / cr, "reject": rest * len(cls_["reject"]) / cr}
        # The mix is a sampling DISTRIBUTION, not a per-frame quota: each of the
        # 99 tile slots draws its class with these probabilities, so frames vary
        # binomially around the mean (an encoder count of 23 +- 4) the way real
        # frames would, instead of every frame carrying the same retention.
        # (--mix-mode exact restores the quota, e.g. for a cost hint.)
        n_frames = a.frames or 3
        keys = ("keep", "cloudy", "reject")
        probs = np.array([share[k] for k in keys]); probs /= probs.sum()
        pools = {k: rng.permutation(np.array(v)) if v else np.array([], int) for k, v in cls_.items()}
        cursor = {k: 0 for k in keys}
        order, comp = [], []
        for fi in range(n_frames):
            if a.mix_mode == "exact":
                per = {k: int(round(probs[j] * N)) for j, k in enumerate(keys)}
                per["keep"] += N - sum(per.values())
                classes = np.repeat(np.arange(3), [per[k] for k in keys])
            else:
                classes = rng.choice(3, size=N, p=probs)
            classes = rng.permutation(classes)               # mixed across the grid
            sel = []
            for c in classes:
                k = keys[c]; pool = pools[k]
                if len(pool) == 0:
                    k = "keep"; pool = pools[k]
                sel.append(int(pool[cursor[k] % len(pool)])); cursor[k] += 1   # cycle a short class
            order.extend(sel)
            comp.append({k: int(np.sum(classes == j)) for j, k in enumerate(keys)})
        order = np.array(order)
        print(f"{n_frames} matched frame(s), mix={a.mix} ({a.mix_mode}): tiles reaching the encoder per frame "
              + ", ".join(str(c["keep"]) for c in comp), flush=True)
    else:
        order = rng.permutation(len(items))
        n_frames = len(order) // (COLS * ROWS)
        if a.frames:
            n_frames = min(n_frames, a.frames)
        print(f"{len(items)} crops from {a.split}; {n_frames} frames of {COLS*ROWS} tiles "
              f"({len(order) - n_frames*COLS*ROWS} crops left over)", flush=True)

    phase, err = find_phase(np.stack([items[i][0] for i in order[:4]]).reshape(2 * TILE, 2 * TILE, 3))
    print(f"Bayer phase reproducing the module's R,G,B order: {phase} (mse {err:.3f})")

    index = []
    for fi in range(n_frames):
        sel = order[fi * COLS * ROWS:(fi + 1) * COLS * ROWS]
        frame = np.zeros((H, W, 3), np.uint16)
        masks = np.zeros((COLS * ROWS, TILE, TILE), np.uint8)
        lcs = np.zeros((COLS * ROWS, TILE, TILE), np.uint8)
        tiles_meta = []
        for k, i in enumerate(sel):
            t, m, g, meta = items[i]
            r, c = divmod(k, COLS)
            frame[r * TILE:(r + 1) * TILE, c * TILE:(c + 1) * TILE] = t
            masks[k] = m
            if g is not None:
                lcs[k] = g
            tiles_meta.append(dict(tile=k, row=r, col=c, **meta))
        # rows the tiler discards: mirror, so the frame has no hard edge there
        rest = H - ROWS * TILE
        frame[ROWS * TILE:] = frame[ROWS * TILE - rest:ROWS * TILE][::-1]
        raw = mosaic(frame[::-1, ::-1], phase)
        # verify through the module's own chain, per tile, against the corpus rendering
        out = module_chain(raw)
        psnrs = []
        for k in range(COLS * ROWS):
            r, c = divmod(k, COLS)
            o = out[r * TILE:(r + 1) * TILE, c * TILE:(c + 1) * TILE].astype(float)
            ref = render(frame[r * TILE:(r + 1) * TILE, c * TILE:(c + 1) * TILE]).astype(float)
            mse = np.mean((o - ref) ** 2)
            psnrs.append(99.0 if mse == 0 else 10 * np.log10(255 ** 2 / mse))
        name = f"{stem}_{fi:02d}"
        raw.astype("<u2").tofile(os.path.join(a.out, name + ".bayerRG"))
        np.savez_compressed(os.path.join(a.out, name + ".labels.npz"), mask=masks, lc=lcs)
        summary = dict(name=name, split=a.split, select=a.select, seed=a.seed, frame_index=fi, gain=GAIN,
                       matched_mix=(comp[fi] if a.select.startswith("agreement") else None),
                       bayer_phase=phase, tiles=tiles_meta,
                       demosaic_psnr_db=dict(mean=float(np.mean(psnrs)), min=float(np.min(psnrs))),
                       cloud_tiles=int(sum(1 for m in tiles_meta if m["thick"] + m["thin"] > 0.5)),
                       water_tiles=int(sum(1 for m in tiles_meta if m.get("lc", {}).get("water", 0) >= 0.6)))
        json.dump(summary, open(os.path.join(a.out, name + ".json"), "w"), indent=1)
        index.append({k: v for k, v in summary.items() if k != "tiles"})
        print(f"  {name}: demosaic-vs-corpus PSNR mean {np.mean(psnrs):.1f} dB (min {np.min(psnrs):.1f}); "
              f"majority-cloud tiles {summary['cloud_tiles']}, water tiles {summary['water_tiles']}", flush=True)
    idx_path = os.path.join(a.out, f"index_{stem[5:]}.json")
    json.dump(index, open(idx_path, "w"), indent=1)
    print(f"wrote {n_frames} frames + labels to {a.out}; index {idx_path}")


if __name__ == "__main__":
    sys.exit(main())
