#!/usr/bin/env python3
"""The effort ladder on both axes: what each rung costs and what it buys.

    analysis/ladder_report.py --models-dir training/out \
        --models landcover_low landcover_medium landcover_high

A rung is an operating point, so it is only justified if it is well separated
from its neighbours in *both* cost and quality. The first ladder we trained was
79.8 / 96.9 / 98.3 -- monotone, but the top two rungs are 1.4 points apart, so
nothing would ever pay for high. That is a property you can only see by putting
cost and accuracy in one table, which is what this does.

Cost here is host-CPU median latency and model size. Both are proxies: the
number that actually schedules is NPU latency on the i.MX8MP, measured on the
board. They rank the candidates correctly, which is all that rung *selection*
needs; the paper's figures come from the board.
"""

import argparse
import json
import os
import sys
import time

import numpy as np
from ai_edge_litert.interpreter import Interpreter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def cost(model_path, reps=30):
    it = Interpreter(model_path=model_path)
    it.allocate_tensors()
    di = it.get_input_details()[0]
    shape = [int(v) for v in di["shape"]]
    rng = np.random.default_rng(43)
    x = rng.integers(0, 256, size=shape, dtype=np.uint8)
    for _ in range(5):                      # warm up
        it.set_tensor(di["index"], x); it.invoke()
    ts = []
    for _ in range(reps):
        t = time.perf_counter()
        it.set_tensor(di["index"], x); it.invoke()
        ts.append((time.perf_counter() - t) * 1000.0)
    return shape[1], os.path.getsize(model_path), float(np.median(ts))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-dir", default="training/out")
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--limit", type=int, default=1500,
                    help="EuroSAT images per model")
    ap.add_argument("--skip-device", action="store_true")
    ap.add_argument("-o", "--out", default="analysis/stage4.5-models/ladder.json")
    a = ap.parse_args()

    import model_isolation as mi
    import device_labelled_eval as dl

    classes = sorted(d for d in os.listdir(mi.DEFAULT_DATA)
                     if os.path.isdir(os.path.join(mi.DEFAULT_DATA, d)))
    files = []
    for i, c in enumerate(classes):
        d = os.path.join(mi.DEFAULT_DATA, c)
        files += [(i, os.path.join(d, f)) for f in sorted(os.listdir(d))
                  if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    # Truncating the sorted list would take only the first classes, so subsample
    # with a fixed seed instead -- the ranking has to be over all ten classes.
    rng = np.random.default_rng(43)
    if a.limit and a.limit < len(files):
        files = [files[i] for i in rng.permutation(len(files))[:a.limit]]
    print(f"{len(files)} EuroSAT images, {len(classes)} classes")
    _lab = json.load(open(os.path.join(HERE, "stage4.5-models", "device_labels.json")))
    labels = _lab.get("labels", _lab)

    rows = []
    for name in a.models:
        p = os.path.join(a.models_dir, f"{name}.tflite")
        if not os.path.exists(p):
            print(f"  missing {p}", file=sys.stderr)
            continue
        res, size, ms = cost(p)
        row = {"model": name, "res": res, "bytes": size, "host_ms": ms}
        if files:
            conf, total, _ = mi.evaluate(p, classes, files, "rgb")
            row["eurosat_top1"] = float(np.trace(conf) / max(total, 1))
        if not a.skip_device:
            per_class, _preds = dl.evaluate(p, labels, dl.DATA)
            hits = [h for v in per_class.values() for h in v]
            row["device_overall"] = float(np.mean(hits)) if hits else float("nan")
            row["device_per_class"] = {k: float(np.mean(v))
                                       for k, v in per_class.items()}
            row["device_n"] = len(hits)
        rows.append(row)

    print(f"\n{'model':22} {'res':>4} {'size':>10} {'host ms':>9} "
          f"{'EuroSAT':>8} {'device':>8}")
    print("-" * 68)
    for r in rows:
        print(f"{r['model']:22} {r['res']:4} {r['bytes']:10,} {r['host_ms']:9.1f} "
              f"{r.get('eurosat_top1', float('nan')):8.1%} "
              f"{r.get('device_overall', float('nan')):8.1%}")

    if len(rows) > 1:
        print("\nrung separation (each vs the one below):")
        for lo, hi in zip(rows, rows[1:]):
            dq = hi.get("eurosat_top1", 0) - lo.get("eurosat_top1", 0)
            dc = hi["host_ms"] / max(lo["host_ms"], 1e-9)
            verdict = "OK" if dq >= 0.03 else "TOO CLOSE -- nobody pays for this"
            print(f"  {lo['model']:20} -> {hi['model']:20} "
                  f"quality {dq:+.1%}, cost x{dc:.1f}   {verdict}")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(rows, open(a.out, "w"), indent=2)
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
