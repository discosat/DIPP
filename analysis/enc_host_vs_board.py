#!/usr/bin/env python3
"""Cross-check the host codecs against the board's on the same frame.

    analysis/enc_host_vs_board.py analysis/stage4.5-models/dev_enc_g050.json

The quality axis of the encoder rungs is measured on the host (libjxl 0.10.2
via imagecodecs 2024.1.1, openjpeg 2.4.0 -- the board's versions) on CloudSEN12
tiles; the cost axis on the board, which encodes a CloudSEN12 frame. This renders
that frame the way the board's demosaic does (uniform gain 0.050, zero offset,
module_1.yaml), encodes its 99 tiles with each config the board ran, and
compares bytes per tile. Agreement means the host numbers describe the deployed
encoder; a systematic gap would mean a version or rendering difference that the
quality figures would inherit.
"""
import json, os, sys, tempfile
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
GAIN = 0.050


def frame_tiles(gain=GAIN, name="cs12_validation_00.bayerRG"):
    import cv2
    from device_labelled_eval import DATA
    from preprocess_replica import H, W, TILE
    raw = np.fromfile(os.path.join(DATA, name), dtype="<u2").reshape(H, W)
    dem = cv2.rotate(cv2.cvtColor(raw, cv2.COLOR_BayerRG2BGR), cv2.ROTATE_180)
    f = np.clip(dem.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    return [np.ascontiguousarray(f[y:y + TILE, x:x + TILE])
            for y in range(0, H - TILE + 1, TILE) for x in range(0, W - TILE + 1, TILE)]


def main():
    d = json.load(open(sys.argv[1]))
    tiles = frame_tiles()
    import imagecodecs
    from encoder_grid import opj_encode
    work = tempfile.mkdtemp(prefix="hvb-")
    print(f"libjxl {imagecodecs.jpegxl_version()}  {len(tiles)} tiles at gain {GAIN}")
    print(f"{'config':18} {'board B/tile':>13} {'host B/tile':>12} {'ratio':>7}")
    for k, v in d.items():
        if k in ("idle", "baseline") or not isinstance(v, dict) or not v.get("encoded_mean_bytes"):
            continue
        parts = k.split(":")
        if len(parts) == 2:                                   # jxl effort:distance
            e, dist = int(parts[0]), float(parts[1])
            hb = np.mean([len(imagecodecs.jpegxl_encode(t, effort=e, distance=dist, numthreads=1)) for t in tiles])
        else:                                                 # j2k ratio:resync:layers:res
            ratio, resync, layers, res = float(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
            sizes = [len(opj_encode(t, work, ratio, res, layers, bool(resync), bool(resync))[0] or b"") for t in tiles]
            hb = np.mean(sizes)
        bb = v["encoded_mean_bytes"]
        print(f"{k:18} {bb:13.0f} {hb:12.0f} {bb/hb:7.3f}")


if __name__ == "__main__":
    sys.exit(main())
