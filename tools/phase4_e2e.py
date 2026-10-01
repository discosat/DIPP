#!/usr/bin/env python3
"""End-to-end runs of the FULL flight config on the board, per static pipeline.

    tools/phase4_e2e.py --pipelines 2 6 3 --n 4 --image cs12_validation_00.bayerRG

Unlike bench_models.py (cut-down pipelines, one stage under test), this pushes
the flight configuration as-is -- every slot the paper's pipelines point at --
and runs the static LOW (2), MEDIUM (6) and HIGH (3) pipelines through all
five stages on one frame. Per launch it records every module's EXEC latency
per batch, the classification decisions, the segmentation kept-pixel lines
and the number of tiles uploaded, i.e. measured retention and the measured
per-frame latency of the composed pipeline. Energy is recorded when the Otii
answers; the composed energy can also be summed from the per-stage rung
measurements with the measured retention.
"""
import argparse, json, os, re, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import campaign  # noqa: E402  (BENCH_RUN_ARGS)
REPO = os.path.dirname(HERE)
FLIGHT = os.path.join(REPO, "experiments", "board", "flight-config")
BOARD_DIR = "/home/root/tmp-experiments"


def board(cmd, timeout=300):
    r = subprocess.run([os.path.join(HERE, "board_console.py"), f"( {cmd} ) < /dev/null"],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def push(local, remote):
    for attempt in range(3):
        r = subprocess.run([os.path.join(HERE, "board_push.py"), local, remote], capture_output=True, timeout=900)
        if r.returncode == 0:
            return True
        time.sleep(5)
    return False


def run(pipeline, n, image, drain, energy, label, idle=0, storage="MEM", env=""):
    out = board(f"cd {BOARD_DIR} && ./shmclean", 60)
    m = re.search(r"SHMCLEAN removed=(\d+) failed=(\d+)", out)
    if m is None or int(m.group(2)):
        sys.exit(f"{label}: shmclean did not report a clean machine: {out[-200:]}")
    floor = {}
    if idle:
        # The floor is read immediately before EACH run: it sits at 3.1-3.3 W
        # for ~20 min after a boot and 2.75 W settled, and a 0.1 W error over
        # a 150 s run is ~90 uWh per frame (tools/energy_calibration2.sh).
        alive = board("pidof dipp || printf 'NO%s\\n' DAEMON", 60)
        if "NODAEMON" not in alive:
            sys.exit(f"{label}: dipp still running; refusing to read the idle floor")
        energy.start(); time.sleep(idle); floor = energy.stop()
        if floor:
            print(f"  idle floor {floor['avg_power_w']:.4f} W over {floor['measured_s']:.1f} s", flush=True)
    energy.start()
    board(f"cd {BOARD_DIR} && ({env}setsid ./run.sh --label {label} --pipeline {pipeline} "
          f"--storage {storage} --heuristic BEST_EFFORT --load populate --n {n} --images 1 "
          f"--no-trace --min-drain {drain} --image {image} {campaign.BENCH_RUN_ARGS} > run_{label}.log 2>&1 &); "
          f"printf 'ST%s\\n' ARTED", 60)
    deadline = time.time() + 180 + n * 200 + drain * 3
    silent = 0
    while time.time() < deadline:
        time.sleep(15)
        try:
            done = board(f"test -f {BOARD_DIR}/runs/{label}/board.json && printf 'B%s\\n' JDONE", 30)
            silent = 0 if done.strip() else silent + 1
        except subprocess.TimeoutExpired:
            done = ""; silent += 1
        if "BJDONE" in done:
            break
        if silent >= 4:            # hard hang: console dead ~2 min
            print(f"  {label}: console silent for {silent} polls; assuming a hard hang", flush=True)
            energy.stop(); sys.exit(3)
    e = energy.stop()
    floor_after = {}
    if idle:                       # bracket a floor shift during the run
        board(f"pkill -TERM dipp; sleep 2; pkill -KILL dipp; cd {BOARD_DIR} && ./shmclean", 60)
        energy.start(); time.sleep(idle); floor_after = energy.stop()
        if floor_after:
            print(f"  idle floor after {floor_after['avg_power_w']:.4f} W", flush=True)
    log = board(f"grep -a '^EXEC ' {BOARD_DIR}/runs/{label}/console.log | "
                f"sed -n 's/.*module=\\([^ ]*\\).*latency_meas_us=\\([0-9]*\\).*/\\1 \\2/p'", 300)
    execs = [(a, int(b)) for a, b in (ln.split() for ln in log.splitlines() if len(ln.split()) == 2)
             if b.isdigit()]
    up = board(f"grep -ac 'Uploading image' {BOARD_DIR}/runs/{label}/console.log", 120)
    digits = [ln.strip() for ln in up.splitlines() if ln.strip().isdigit()]
    n_up = int(digits[-1]) if digits else 0
    # classification decisions and segmentation kept-pixel lines from the module logs
    # ORDERED: the classifier logs one 'Top class' per tile in tiler order (99
    # per frame), so line k is tile k of the frame -- which is what ties a
    # decision to its expert mask and land cover in the frame's .json/.labels.
    # run.sh archives predictions.txt per run; 20 per line so the serial
    # console's line wrap cannot split the stream.
    cls = board(f"sed -n 's/.*Top class: \\([0-9]*\\).*/\\1/p' {BOARD_DIR}/runs/{label}/predictions.txt | "
                f"awk '{{printf \"%s \", $1; if (NR%20==0) printf \"\\n\"}} END {{printf \"\\n\"}}'", 300)
    seg = board(f"cat /home/root/logs/tflite_segmentation_* 2>/dev/null | grep -a 'Kept pixels' | "
                f"sed -n 's/.*Kept pixels: \\([0-9]*\\) out of \\([0-9]*\\).*/\\1 \\2/p'", 300)
    kept = [(int(a), int(b)) for a, b in (ln.split() for ln in seg.splitlines() if len(ln.split()) == 2) if a.isdigit()]
    preds = []
    for ln in cls.splitlines():
        toks = ln.split()
        if toks and all(t.isdigit() for t in toks):
            preds += [int(t) for t in toks]
    hist = {}
    for v in preds:
        hist[v] = hist.get(v, 0) + 1
    return dict(pipeline=pipeline, n=n, image=image, energy=e, idle=floor, idle_after=floor_after, execs=execs, uploaded=n_up,
                cls_hist=hist, cls_preds=preds, seg_kept=kept)


def summarise(r):
    from collections import defaultdict
    per = defaultdict(list)
    for m, us in r["execs"]:
        per[m].append(us)
    batches = len(per.get("libdemosaic", []))
    print(f"pipeline {r['pipeline']} on {r['image']}: {batches} batches, uploaded {r['uploaded']} tiles "
          f"({r['uploaded']/max(batches,1):.1f}/frame)")
    total = 0
    for m in ("libdemosaic", "libtile", "libtflite_classification", "libtflite_binary_segmentation",
              "libjpegxl_module", "libjpeg2000_module"):
        v = sorted(per.get(m, []))
        if not v:
            continue
        med = v[len(v) // 2] / 1000
        total += med
        print(f"    {m:32} {len(v):2} x  median {med:8.1f} ms")
    print(f"    {'composed (sum of medians)':32}     {total:8.1f} ms/frame")
    kept = r["seg_kept"]
    if kept:
        keep = sum(1 for a, b in kept if a > 0.5 * b)
        print(f"    classification kept (tiles seen by seg): {len(kept)} = {len(kept)/max(batches,1):.1f}/frame; "
              f"seg kept {keep} ({100*keep/len(kept):.0f}%)")
    if r["cls_hist"]:
        print(f"    class histogram: {dict(sorted(r['cls_hist'].items()))}")
    if r["energy"]:
        print(f"    run energy {r['energy']['energy_wh']:.5f} Wh over {r['energy']['measured_s']:.1f} s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipelines", nargs="+", type=int, default=[2, 6, 3])
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--images", nargs="+", default=["cs12_validation_00.bayerRG"])
    ap.add_argument("--drain", type=int, default=160)
    ap.add_argument("--no-push", action="store_true", help="flight vmem already on the board")
    ap.add_argument("--storage", default="MEM", choices=["MEM", "MMAP", "HYBRID"])
    ap.add_argument("--env", default="", help="env assignments prefixed to the run, e.g. 'DIPP_HALT_AFTER_MODULE=2 '")
    ap.add_argument("--tag", default="", help="label suffix, so a second run of the same "
                    "pipeline and frame does not overwrite the first")
    ap.add_argument("--idle", type=int, default=0, metavar="SECONDS",
                    help="read the idle floor for this long immediately before each run")
    ap.add_argument("-o", "--out", default=os.path.join(REPO, "analysis", "stage4.5-models", "e2e_runs.json"))
    a = ap.parse_args()
    import campaign
    energy = campaign.Energy()
    if not a.no_push:
        vmem = os.path.join(tempfile.gettempdir(), "flight.vmem")
        subprocess.run([sys.executable, os.path.join(HERE, "make_storage_vmem.py"), "build",
                        os.path.join(FLIGHT, "manifest.yaml"), "-o", vmem], check=True, capture_output=True)
        if not push(vmem, f"{BOARD_DIR}/storage.vmem"):
            sys.exit("could not push the flight config")
        print("flight config pushed (all slots)")
    results = json.load(open(a.out)) if os.path.exists(a.out) else {}
    for image in a.images:
        for p in a.pipelines:
            stem = image.replace(".bayerRG", "")
            label = f"e2e-p{p}-{stem}" + (f"-{a.tag}" if a.tag else "")
            board(f"rm -f /home/root/logs/tflite_*", 60)
            r = run(p, a.n, image, a.drain, energy, label, a.idle, a.storage, a.env)
            results[f"p{p}/{stem}" + (f"/{a.tag}" if a.tag else "")] = r
            summarise(r)
            json.dump(results, open(a.out, "w"), indent=1)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    sys.exit(main())
