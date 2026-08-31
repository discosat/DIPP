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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FLIGHT = os.path.join(ROOT, "experiments", "board", "flight-config")
BOARD_DIR = "/home/root/tmp-experiments"
sys.path.insert(0, HERE)

PIPE_FULL = """pipeline:
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

MODULE3 = """latency_cost: 235000
energy_cost: 465
parameters:
- key: model_filename
  type: 5
  value: /usr/share/models/{model}.tflite
- key: class_index
  type: 3
  value: 7
"""


def board(cmd, timeout=300):
    r = subprocess.run([os.path.join(HERE, "board_console.py"), cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def make_vmem(model, out_path):
    """A full config set with pipeline 2 cut down and slot 3 repointed."""
    tmp = tempfile.mkdtemp(prefix="benchcfg-")
    for f in os.listdir(FLIGHT):
        if f.endswith(".yaml"):
            shutil.copy(os.path.join(FLIGHT, f), tmp)
    open(os.path.join(tmp, "pipeline_2.yaml"), "w").write(
        PIPE_BASE if model is None else PIPE_FULL)
    if model is not None:
        open(os.path.join(tmp, "module_3.yaml"), "w").write(
            MODULE3.format(model=model))
    man = os.path.join(tmp, "manifest.yaml")
    # The manifest lists files relative to itself, so the copied dir just works.
    subprocess.run([sys.executable, os.path.join(HERE, "make_storage_vmem.py"),
                    "build", man, "-o", out_path], check=True,
                   capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)


EXEC_RE = re.compile(r"^EXEC .*module=(\S+).*latency_meas_us=(\d+)", re.M)


def run_once(label, n, energy):
    board(f"cd {BOARD_DIR} && rm -rf runs/{label}", 60)
    energy.start()
    board(f"cd {BOARD_DIR} && (setsid ./run.sh --label {label} --pipeline 2 "
          f"--storage MEM --heuristic BEST_EFFORT --load populate "
          f"--n {n} --images 1 --no-trace > run_{label}.log 2>&1 &) ; "
          f"printf 'ST%s\\n' ARTED", 60)
    import time
    deadline = time.time() + 120 + n * 60
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
    return {"classify_us": lat, "demosaic_us": dem, "energy": e}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=[])
    ap.add_argument("--baseline", action="store_true")
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
        make_vmem(model, vmem)
        subprocess.run([os.path.join(HERE, "board_push.py"), vmem,
                        f"{BOARD_DIR}/storage.vmem"], check=True,
                       capture_output=True, timeout=900)
        r = run_once(label, a.n, energy)
        key = model or "baseline"
        results[key] = r
        lat = r["classify_us"]
        med = sorted(lat)[len(lat) // 2] / 1000.0 if lat else float("nan")
        wh = (r["energy"] or {}).get("energy_wh")
        print(f"{key:20} classify median {med:8.1f} ms over {len(lat)} batches"
              + (f"   run energy {wh:.5f} Wh" if wh else ""))

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
