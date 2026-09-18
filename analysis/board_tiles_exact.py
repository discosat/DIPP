#!/usr/bin/env python3
"""Pixel-exact check of the board's demosaic + tile chain against the host.

    analysis/board_tiles_exact.py analysis/stage4.5-models/exact_check.json

The bench run recorded the per-tile JPEG XL sizes the board produced for one
frame (--kind encoding, effort 3, distance 2.358). libjxl at a fixed distance
is deterministic in its input, so if the host renders the same frame the way
the module does and encodes each tile with the same libjxl, the 99 sizes must
match EXACTLY. Two host renderings are tried -- truncation after the gain
(what the training/scoring scripts do) and round-to-nearest (what
convertTo's saturate_cast does) -- so the check also says which convention
the board follows.
"""
import json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FRAMES = os.path.expanduser("~/dipp-datasets/cs12-frames")
GAIN = 0.050


def main():
    import cv2, imagecodecs
    d = json.load(open(sys.argv[1]))
    key = [k for k in d if k not in ("idle", "baseline")][0]
    board = d[key]["encoded_bytes"][-99:]
    name = sys.argv[2] if len(sys.argv) > 2 else "cs12_test_matchedkeep_00.bayerRG"
    raw = np.fromfile(os.path.join(FRAMES, name), dtype="<u2").reshape(2056, 2464)
    dem = cv2.rotate(cv2.cvtColor(raw, cv2.COLOR_BayerRG2BGR), cv2.ROTATE_180).astype(np.float32) * GAIN
    e, dist = key.split(":")
    for conv, f in (("truncate", np.clip(dem, 0, 255).astype(np.uint8)),
                    ("round", np.clip(np.rint(dem), 0, 255).astype(np.uint8))):
        host = [len(imagecodecs.jpegxl_encode(np.ascontiguousarray(f[y:y+224, x:x+224]), effort=int(e), distance=float(dist), numthreads=1))
                for y in range(0, 2056 - 223, 224) for x in range(0, 2464 - 223, 224)]
        eq = sum(1 for a, b in zip(host, board) if a == b)
        print(f"{conv:9} host vs board: {eq}/99 tiles identical in size; mean {np.mean(host):.1f} vs {np.mean(board):.1f} B")
    print(f"libjxl {imagecodecs.jpegxl_version()}")


if __name__ == "__main__":
    sys.exit(main())
