#!/usr/bin/env python3
"""Measure what each candidate model actually costs on the board.

    tools/bench_models.py --models bench_cnn064 bench_a035r128 [--n 3]
    tools/bench_models.py --baseline            # demosaic+tile only, for energy

A rung is an operating point, so it has to be separated from its neighbours on
all three axes -- accuracy, latency and energy. Accuracy comes from the host
(EuroSAT + the labelled device tiles); latency and energy can only come from
here. Host-CPU timing is not a substitute and is actively misleading: it ranks
MobileNetV2 alpha=0.35 at 96px as both the fastest and the most accurate
candidate, which cannot be right on an NPU whose cost structure is nothing like
XNNPACK's.

Method. Each model is dropped into slot 3 of a cut-down pipeline
(demosaic -> tile -> classification), which is generated as a real
`storage.vmem` so the daemon is configured exactly as it would be in flight.
The board runs it over the same frame; `latency_meas_us` on the classification
EXEC line is the per-batch NPU time for 99 tiles. Energy is the Otii reading
for the run minus a `--baseline` run of demosaic+tile alone -- the difference
is the classification work. That is the cheap version of Stage 5's
characterisation, and good enough to *choose* rungs; the paper's numbers come
from the rigorous cumulative-differencing pass.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FLIGHT = os.path.join(ROOT, "experiments", "board", "flight-config")
BOARD_DIR = "/home/root/tmp-experiments"
sys.path.insert(0, HERE)

PIPE_CLASSIFY = """pipeline:
- name: libdemosaic
  implementations:
  - param_id: 1
    effort_level: default
- name: libtile
  implementations:
  - param_id: 2
    effort_level: default
- name: libtflite_classification
  implementations:
  - param_id: 3
    effort_level: default
"""

PIPE_ENCODE = PIPE_CLASSIFY.replace(
    "libtflite_classification", "libjpegxl_module").replace(
    "param_id: 3", "param_id: 11")

PIPE_J2K = PIPE_CLASSIFY.replace(
    "libtflite_classification", "libjpeg2000_module").replace(
    "param_id: 3", "param_id: 16")

PIPE_SEGMENT = PIPE_CLASSIFY.replace(
    "libtflite_classification", "libtflite_binary_segmentation").replace(
    "param_id: 3", "param_id: 6")

PIPE_BASE = """pipeline:
- name: libdemosaic
  implementations:
  - param_id: 1
    effort_level: default
- name: libtile
  implementations:
  - param_id: 2
    effort_level: default
"""

MODULE_CLASSIFY = """latency_cost: 235000
energy_cost: 465
parameters:
- key: model_filename
  type: 5
  value: /usr/share/models/{model}.tflite
- key: class_index
  type: 3
  value: 7
"""

# Segmentation keeps class 0 (the non-cloud side) and a permissive threshold, so
# the module always emits something and timing never depends on the scene.
MODULE_SEGMENT = """latency_cost: 120000
energy_cost: 264
parameters:
- key: model_filename
  type: 5
  value: /usr/share/models/{model}.tflite
- key: class_index
  type: 3
  value: 0
- key: threshold_percentage
  type: 4
  value: 0.0
"""

# The encoder's "model" is a setting pair, effort:distance, since one binary
# serves all three rungs. distance 0 means lossless.
MODULE_ENCODE = """latency_cost: 149000
energy_cost: 264
parameters:
- key: effort_level
  type: 3
  value: {effort}
- key: distance
  type: 4
  value: {distance}
"""

# JPEG 2000 rungs are addressed by compression ratio; 1 means lossless.
MODULE_J2K = """latency_cost: 149000
energy_cost: 264
parameters:
- key: compression_ratio
  type: 4
  value: {ratio}
- key: resolutions
  type: 3
  value: {resolutions}
- key: quality_layers
  type: 3
  value: {layers}
- key: resync_markers
  type: 3
  value: {resync}
"""

KINDS = {
    "classification": dict(pipe=PIPE_CLASSIFY, module=MODULE_CLASSIFY, slot=3,
                           log="tflite_classification", mark="Top class"),
    # The .so is libtflite_binary_segmentation but the module names its own log
    # "tflite_segmentation_<epoch>.txt" -- the two do not match, so the prefix
    # has to come from the source, not from the module name.
    "segmentation":   dict(pipe=PIPE_SEGMENT,   module=MODULE_SEGMENT,  slot=6,
                           log="tflite_segmentation", mark="Kept pixels"),
    # The encoder writes no log of its own, so timing comes from the EXEC line.
    # With no classification stage ahead of it every tile reaches the encoder,
    # so the batch is never empty and EXEC is always emitted.
    "encoding":       dict(pipe=PIPE_ENCODE,    module=MODULE_ENCODE,   slot=11,
                           log=None, mark=None),
    "j2k":            dict(pipe=PIPE_J2K,       module=MODULE_J2K,      slot=16,
                           log=None, mark=None),
}


def board(cmd, timeout=300):
    r = subprocess.run([os.path.join(HERE, "board_console.py"), cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def make_vmem(model, out_path, kind="classification"):
    """A full config set with pipeline 2 cut down and slot 3 repointed."""
    tmp = tempfile.mkdtemp(prefix="benchcfg-")
    for f in os.listdir(FLIGHT):
        if f.endswith(".yaml"):
            shutil.copy(os.path.join(FLIGHT, f), tmp)
    k = KINDS[kind]
    open(os.path.join(tmp, "pipeline_2.yaml"), "w").write(
        PIPE_BASE if model is None else k["pipe"])
    if model is not None:
        if kind == "encoding":
            effort, distance = model.split(":")
            body = k["module"].format(effort=effort, distance=distance)
        elif kind == "j2k":
            # "ratio[:resync[:layers[:resolutions]]]"
            ratio, resync, layers, res = (model.split(":") + ["0", "1", "6"])[:4]
            body = k["module"].format(ratio=ratio, resync=resync,
                                      layers=layers, resolutions=res)
        else:
            body = k["module"].format(model=model)
        open(os.path.join(tmp, f"module_{k['slot']}.yaml"), "w").write(body)
    man = os.path.join(tmp, "manifest.yaml")
    # The manifest lists files relative to itself, so the copied dir just works.
    subprocess.run([sys.executable, os.path.join(HERE, "make_storage_vmem.py"),
                    "build", man, "-o", out_path], check=True,
                   capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)


EXEC_RE = re.compile(r"^EXEC .*module=(\S+).*latency_meas_us=(\d+)", re.M)


def run_once(label, n, energy, kind="classification", min_drain=20):
    board(f"cd {BOARD_DIR} && rm -rf runs/{label}", 60)
    energy.start()
    board(f"cd {BOARD_DIR} && (setsid ./run.sh --label {label} --pipeline 2 "
          f"--storage MEM --heuristic BEST_EFFORT --load populate "
          f"--n {n} --images 1 --no-trace --min-drain {min_drain} "
          f"> run_{label}.log 2>&1 &) ; "
          f"printf 'ST%s\\n' ARTED", 60)
    import time
    deadline = time.time() + 120 + n * 60 + min_drain * 3
    while time.time() < deadline:
        time.sleep(15)
        if "BJDONE" in board(f"test -f {BOARD_DIR}/runs/{label}/board.json && "
                             f"printf 'B%s\\n' JDONE", 30):
            break
    e = energy.stop()
    log = board(f"cat {BOARD_DIR}/runs/{label}/console.log", 300)
    lat = [int(m.group(2)) for m in EXEC_RE.finditer(log)
           if "classification" in m.group(1)]
    dem = [int(m.group(2)) for m in EXEC_RE.finditer(log)
           if "demosaic" in m.group(1)]

    # The EXEC line only appears if the module returns a non-empty batch, and
    # the batch is empty whenever no tile matches class_index -- which is the
    # normal outcome of running a Residential filter over open sea. Timing must
    # not depend on the scene, so it also comes from the module's own log,
    # which timestamps every tile regardless of what survives the filter.
    k = KINDS[kind]
    if k["log"] is None:
        # Encoder: no module log, but the daemon reports what it uploaded, which
        # is the compressed size we actually care about for downlink volume.
        up = board(f"grep -a 'Uploading image' "
                   f"{BOARD_DIR}/runs/{label}/console.log | tail -n 200", 300)
        sizes = [int(m) for m in re.findall(r"image size = (\d+)", up)]
        return {"classify_us": lat, "demosaic_us": dem, "energy": e,
                "module_log": "", "encoded_bytes": sizes}
    span = board(
        f"L=$({{ ls -t /home/root/logs/{k['log']}_* | head -n 1; }}); "
        f"grep -a '{k['mark']}' $L | head -n 1; "
        f"grep -a '{k['mark']}' $L | tail -n 1; "
        f"grep -ac '{k['mark']}' $L", 300)
    return {"classify_us": lat, "demosaic_us": dem, "energy": e,
            "module_log": span}


TS_RE = re.compile(r"\[\d{4}-\d{2}-\d{2} (\d{2}):(\d{2}):(\d{2})\.(\d{6})\]")


def span_ms(text):
    """Wall time across all tiles, from the module's own timestamps."""
    ts = [((int(h) * 3600 + int(m) * 60 + int(s)) * 1_000_000 + int(us))
          for h, m, s, us in TS_RE.findall(text)]
    n = None
    for line in text.splitlines():
        t = line.strip()
        if t.isdigit():
            n = int(t)
    if len(ts) < 2 or not n or n < 2:
        return None, n
    # ts are first and last tile; scale to all n tiles.
    return (max(ts) - min(ts)) / 1000.0 * n / (n - 1), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=[])
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--kind", default="classification", choices=list(KINDS),
                    help="which pipeline stage to put the model in")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("-o", "--out", default="analysis/stage4.5-models/device_cost.json")
    a = ap.parse_args()

    import campaign
    energy = campaign.Energy()

    jobs = ([(None, "bench-baseline")] if a.baseline else []) + \
           [(m, f"bench-{m}") for m in a.models]
    results = {}
    for model, label in jobs:
        vmem = os.path.join(tempfile.gettempdir(), "storage.vmem")
        make_vmem(model, vmem, a.kind)
        # Serial pushes fail transiently often enough that one bad transfer
        # would otherwise abandon the rest of a multi-model sweep.
        for attempt in range(3):
            r = subprocess.run([os.path.join(HERE, "board_push.py"), vmem,
                                f"{BOARD_DIR}/storage.vmem"],
                               capture_output=True, timeout=900)
            if r.returncode == 0:
                break
            print(f"    push failed (attempt {attempt + 1}/3), retrying")
            time.sleep(5)
        else:
            print(f"  {model or 'baseline'}: SKIPPED (vmem push failed 3x)")
            continue
        r = run_once(label, a.n, energy, a.kind, a.min_drain)
        key = model or "baseline"
        results[key] = r
        lat = r["classify_us"]
        med = sorted(lat)[len(lat) // 2] / 1000.0 if lat else float("nan")
        enc = r.get("encoded_bytes") or []
        ms, ntiles = span_ms(r.get("module_log", ""))
        if enc:
            r["encoded_mean_bytes"] = sum(enc) / len(enc)
            r["encoded_n"] = len(enc)
        r["module_span_ms"] = ms
        r["module_tiles"] = ntiles
        wh = (r["energy"] or {}).get("energy_wh")
        tail = (f"   encoded {r['encoded_mean_bytes']:.0f} B/tile "
                f"(n={r['encoded_n']})" if enc else
                f"   module {ms if ms else float('nan'):8.1f} ms / {ntiles} tiles")
        print(f"{key:22} EXEC {med:8.1f} ms ({len(lat)} batches){tail}"
              + (f"   run {wh:.5f} Wh" if wh else ""))

    if os.path.exists(a.out):
        prev = json.load(open(a.out))
        prev.update(results)
        results = prev
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(results, open(a.out, "w"), indent=2)
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
