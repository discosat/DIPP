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
FLIGHT = os.environ.get("FLIGHT_CONFIG", os.path.join(ROOT, "experiments", "board", "flight-config"))
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

# Segmentation followed by the encoder. The mask fill is a claim about
# COMPRESSIBILITY -- low-pass fill measured -28% for JPEG 2000 on the host
# against blacking cloud out -- and that is invisible to a pipeline that stops
# at segmentation. Slot 16 (J2K low) comes from the flight config unchanged.
PIPE_SEG_ENC = """pipeline:
- name: libdemosaic
  implementations:
  - param_id: 1
    effort_level: default
- name: libtile
  implementations:
  - param_id: 2
    effort_level: default
- name: libtflite_binary_segmentation
  implementations:
  - param_id: 6
    effort_level: default
- name: libjpeg2000_module
  implementations:
  - param_id: 16
    effort_level: default
"""

PIPE_SEG_JXL = PIPE_SEG_ENC.replace(
    "libjpeg2000_module", "libjpegxl_module").replace(
    "param_id: 16", "param_id: 11")

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

# train_res and class_index as in flight-config/module_3..5.yaml: the 224->56->224
# resize is part of the module's measured cost, and water is the target.
MODULE_CLASSIFY = """latency_cost: 235000
energy_cost: 465
parameters:
- key: model_filename
  type: 5
  value: /usr/share/models/{model}.tflite
- key: class_index
  type: 3
  value: {class_index}
- key: train_res
  type: 3
  value: 56
"""

# Segmentation keeps class 0 (the non-cloud side) and a permissive threshold, so
# the module always emits something and timing never depends on the scene.
MODULE_SEG_FULL = """latency_cost: 120000
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
- key: pixel_threshold
  type: 4
  value: {pixel_threshold}
- key: mask_lowpass
  type: 3
  value: {mask_lowpass}
- key: use_npu
  type: 3
  value: 1
"""

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
  value: {threshold}
- key: use_npu
  type: 3
  value: {use_npu}
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
                           exec_name="classification",
                           log="tflite_classification", mark="Top class"),
    # The .so is libtflite_binary_segmentation but the module names its own log
    # "tflite_segmentation_<epoch>.txt" -- the two do not match, so the prefix
    # has to come from the source, not from the module name.
    "segmentation":   dict(pipe=PIPE_SEGMENT,   module=MODULE_SEGMENT,  slot=6,
                           exec_name="segmentation",
                           log="tflite_segmentation", mark="Kept pixels"),
    # The encoder writes no log of its own, so timing comes from the EXEC line.
    # With no classification stage ahead of it every tile reaches the encoder,
    # so the batch is never empty and EXEC is always emitted.
    "encoding":       dict(pipe=PIPE_ENCODE,    module=MODULE_ENCODE,   slot=11,
                           exec_name="jpegxl",
                           log=None, mark=None),
    "j2k":            dict(pipe=PIPE_J2K, exec_name="jpeg2000",       module=MODULE_J2K,      slot=16,
                           log=None, mark=None),
    # Segmentation + encoder: validates pixel_threshold (kept pixels) and
    # mask_lowpass (encoded bytes) on the device, both so far only host-checked.
    # Same, but JPEG XL at a fixed Butteraugli distance. JPEG 2000 here runs at
    # a fixed compression_ratio, which TARGETS a byte budget, so every config
    # encodes to ~3010 B whatever the content and the fill's effect on
    # compressibility is invisible by construction. A quality-targeted encoder
    # lets the bytes move, which is the whole measurement.
    "segjxl":         dict(pipe=PIPE_SEG_JXL, exec_name="jpegxl",
                           module=MODULE_SEG_FULL, slot=6,
                           log="tflite_segmentation", mark="Kept pixels"),
    "segenc":         dict(pipe=PIPE_SEG_ENC, exec_name="jpeg2000",
                           module=MODULE_SEG_FULL, slot=6,
                           log="tflite_segmentation", mark="Kept pixels"),
}


def board(cmd, timeout=300):
    # Every command runs in a subshell with stdin closed: any tool that falls
    # back to stdin when its file argument is empty (grep, cat, dd...) would
    # otherwise read the console itself and wedge it for every later command.
    r = subprocess.run([os.path.join(HERE, "board_console.py"),
                        f"( {cmd} ) < /dev/null"],
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
        elif kind in ("segenc", "segjxl"):
            # "model:pixel_threshold:mask_lowpass"
            name, pt, lp = (model.split(":") + ["0.5", "5"])[:3]
            body = k["module"].format(model=name, pixel_threshold=pt,
                                      mask_lowpass=lp)
        elif kind == "segmentation":
            # "model[:use_npu]" -- the VX delegate refuses some graphs
            # outright ("Failed to verify graph"), so CPU has to be reachable
            # from the bench to tell a broken model from a broken delegate.
            # "model[:use_npu[:threshold_percentage]]". threshold 0.0 keeps
            # every tile (99 raw uploads, 15 MB -- matches the baseline's
            # I/O), 1.0 keeps none (0 uploads); the pair isolates the upload
            # cost, which the energy differencing must account for.
            name, npu, thr = (model.split(":") + ["1", "0.0"])[:3]
            body = k["module"].format(model=name, use_npu=npu, threshold=thr)
        else:
            # classification: "model[:class_index]"; class 10 never matches
            # (10 classes, 0-9) so nothing is retained or uploaded.
            name, cidx = (model.split(":") + ["9"])[:2]
            body = k["module"].format(model=name, class_index=cidx)
        open(os.path.join(tmp, f"module_{k['slot']}.yaml"), "w").write(body)
    man = os.path.join(tmp, "manifest.yaml")
    # The manifest lists files relative to itself, so the copied dir just works.
    subprocess.run([sys.executable, os.path.join(HERE, "make_storage_vmem.py"),
                    "build", man, "-o", out_path], check=True,
                   capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)


EXEC_RE = re.compile(r"^EXEC .*module=(\S+).*latency_meas_us=(\d+)", re.M)


def run_once(label, n, energy, kind="classification", min_drain=20, env="",
             image="", a_idle_after=0):
    board(f"cd {BOARD_DIR} && rm -rf runs/{label}", 60)
    # Reclaim leaked SysV segments before measuring. DIPP's STORAGE_MEM path
    # detaches without IPC_RMID (the MMAP conversion path does RMID; MEM never
    # does), so segments accumulate across configs -- 61 segments holding
    # 1.59 GB of the board's 1.95 GB were found after one grid sweep, leaving
    # 46 MB available. At that point demosaic, the first module and the one
    # carrying the full-resolution frame, dies with MODULE_EXIT_CRASH and every
    # config fails identically regardless of its parameters. That looked exactly
    # like a codec bug and is not one. Fixing the leak itself is A8/Stage 5;
    # this keeps each measurement on an unpolluted machine meanwhile.
    out = board(f"cd {BOARD_DIR} && ./shmclean", 60)
    m = re.search(r"SHMCLEAN removed=(\d+) failed=(\d+) bytes=(\d+)", out)
    if m is None:
        sys.exit(f"{label}: shmclean did not report; refusing to measure on an "
                 f"unknown machine state")
    if int(m.group(2)):
        sys.exit(f"{label}: shmclean failed to remove {m.group(2)} segments")
    if int(m.group(1)):
        print(f"  reclaimed {m.group(1)} SHM segments, "
              f"{int(m.group(3))/2**20:.0f} MiB", flush=True)
    energy.start()
    # The classification module takes its CPU/NPU switch from the environment
    # (DIPP_NO_NPU) rather than a config key, so telling "this model is broken"
    # from "this delegate mishandles this model" needs the variable to reach the
    # daemon the run script starts.
    board(f"cd {BOARD_DIR} && ({env}setsid ./run.sh --label {label} --pipeline 2 "
          f"--storage MEM --heuristic BEST_EFFORT --load populate "
          f"--n {n} --images 1 --no-trace --min-drain {min_drain} "
          + (f"--image {image} " if image else "") +
          f"> run_{label}.log 2>&1 &) ; "
          f"printf 'ST%s\\n' ARTED", 60)
    import time
    deadline = time.time() + 120 + n * 60 + min_drain * 3
    silent = 0
    while time.time() < deadline:
        time.sleep(15)
        # A transient serial stall must not abort a 90 s encode run that is
        # otherwise fine: tolerate a poll timeout and try again. A console that
        # stays dead is the hard hang (2026-09-14/15: two overnight runs sat
        # for 56 min each on a board that never printed a byte), so give up
        # after ~2 min of silence and let the caller power-cycle.
        try:
            done = board(f"test -f {BOARD_DIR}/runs/{label}/board.json && "
                         f"printf 'B%s\\n' JDONE", 30)
            silent = 0 if done.strip() else silent + 1
        except subprocess.TimeoutExpired:
            done = ""; silent += 1
        if "BJDONE" in done:
            break
        if silent >= 4:
            print(f"  {label}: console silent for {silent} polls; assuming a "
                  f"hard hang", flush=True)
            energy.stop()
            sys.exit(3)
    e = energy.stop()
    # The floor can shift DURING a run (3.03-3.30 W for up to ~45 min after a
    # boot, 2.75 W after), and a shift is invisible from one reading. A second
    # reading after the run brackets it: the summariser flags runs whose two
    # floors disagree by more than 0.03 W (~25 uWh/frame over 150 s).
    floor_after = {}
    if a_idle_after:
        board("pkill -TERM dipp; sleep 2; pkill -KILL dipp; " + f"cd {BOARD_DIR} && ./shmclean", 60)
        energy.start(); time.sleep(a_idle_after); floor_after = energy.stop()
        if floor_after:
            print(f"{'idle after':22} {floor_after['avg_power_w']:.4f} W over "
                  f"{floor_after['measured_s']:.1f} s", flush=True)
    # Reduce on the board, not here. `cat console.log` over the serial console
    # silently truncates once the log gets big: a 50-batch run that the board
    # reported as "executions=141 completed=47" came back with 5 EXEC lines
    # parseable, so every multi-batch measurement was quietly reading a fraction
    # of its own data and dividing by the wrong count. Extracting just
    # "module latency" pairs turns ~400-byte lines into ~30-byte ones and keeps
    # the transfer well inside one read.
    log = board(f"grep -a '^EXEC ' {BOARD_DIR}/runs/{label}/console.log | "
                f"sed -n 's/.*module=\\([^ ]*\\).*latency_meas_us=\\([0-9]*\\).*/\\1 \\2/p'",
                300)
    pairs = [ln.split() for ln in log.splitlines() if len(ln.split()) == 2
             and ln.split()[1].isdigit()]
    # Match the stage under test, not always classification: the encoder's EXEC
    # line names libjpegxl_module, so a hardcoded filter reported 0 batches and
    # a nan latency for every encoder run.
    want = KINDS[kind].get("exec_name", "classification")
    lat = [int(v) for m, v in pairs if want in m]
    dem = [int(v) for m, v in pairs if "demosaic" in m]

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
                "idle_after": floor_after, "module_log": "", "encoded_bytes": sizes}
    # Guard the empty case: a baseline run leaves no module log, and
    # `grep PATTERN $L` with L empty reads the CONSOLE as stdin and never
    # returns -- every later push was typed into that grep. That is what
    # "vmem push failed 3x" after a baseline was, twice.
    span = board(
        f"L=$({{ ls -t /home/root/logs/{k['log']}_* 2>/dev/null | head -n 1; }}); "
        f"if [ -n \"$L\" ]; then "
        f"grep -a '{k['mark']}' \"$L\" | head -n 1; "
        f"grep -a '{k['mark']}' \"$L\" | tail -n 1; "
        f"grep -ac '{k['mark']}' \"$L\"; else echo NOLOG; fi", 300)
    return {"classify_us": lat, "demosaic_us": dem, "energy": e,
            "idle_after": floor_after, "module_log": span}


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
    # Counting completions cannot distinguish "slow" from "finished": a module
    # slower than the stability window leaves the count at zero, which looks
    # stable, and the run is cut off mid-encode. Keep draining until this many
    # seconds have passed while nothing has completed.
    ap.add_argument("--min-drain", type=int, default=20,
                    help="seconds to keep draining while nothing has completed")
    # Per-module energy is a DIFFERENCE against a run that did less work, and
    # the two runs do not last the same wall-clock time (55.9 s baseline vs
    # 87.5 s for a slow encoder). Subtracting the raw readings therefore charges
    # the module for the extra idle seconds it was merely present for. The
    # correction needs the board's idle draw, which nothing measured, so this
    # records it: P_idle, with the daemon stopped, on the same machine state as
    # the runs it will be subtracted from.
    ap.add_argument("--idle-after", type=int, default=0, metavar="SECONDS",
                    help="also read the idle floor AFTER each run, to bracket a "
                         "floor shift during it")
    ap.add_argument("--idle", type=int, default=0, metavar="SECONDS",
                    help="record idle power for this long and store it as the "
                         "P_idle used to difference per-module energy")
    ap.add_argument("--image", default="", help="scene to feed, e.g. "
                    "koge-coast-full-clouds.bayerRG; the mask fill does nothing "
                    "measurable on a cloud-free frame")
    ap.add_argument("--env", default="", help="env assignments prefixed to the "
                    "run, e.g. 'DIPP_NO_NPU=1 '")
    ap.add_argument("-o", "--out", default="analysis/stage4.5-models/device_cost.json")
    a = ap.parse_args()

    import campaign
    energy = campaign.Energy()
    results = {}

    if a.idle:
        # A leftover daemon would make this measure a workload, not an idle
        # floor, and it would do so silently.
        alive = board("pidof dipp || printf 'NO%s\\n' DAEMON", 60)
        if "NODAEMON" not in alive:
            sys.exit("refusing to measure idle power: dipp is still running")
        board(f"cd {BOARD_DIR} && ./shmclean", 60)
        print(f"measuring idle power for {a.idle} s (daemon stopped)", flush=True)
        energy.start()
        time.sleep(a.idle)
        e = energy.stop()
        results["idle"] = {"energy": e}
        if e:
            print(f"{'idle':22} {e['avg_power_w']:.4f} W over "
                  f"{e['measured_s']:.1f} s", flush=True)
        else:
            print("  [energy] no idle reading available", flush=True)

    jobs = ([(None, "bench-baseline")] if a.baseline else []) + \
           [(m, f"bench-{m}") for m in a.models]
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
            # A skipped model must fail the sweep: three models were once
            # silently skipped after a failed push wedged the console, and the
            # run reported ALL DONE.
            print(f"  {model or 'baseline'}: SKIPPED (vmem push failed 3x)")
            results.setdefault("_failed", []).append(model or "baseline")
            continue
        r = run_once(label, a.n, energy, a.kind, a.min_drain, a.env,
                     a.image, a.idle_after)
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

    failed = results.pop("_failed", [])
    if os.path.exists(a.out):
        prev = json.load(open(a.out))
        prev.update(results)
        results = prev
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(results, open(a.out, "w"), indent=2)
    print(f"\nwrote {a.out}")
    if failed:
        print(f"FAILED: {', '.join(failed)} not measured (vmem push failed)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
