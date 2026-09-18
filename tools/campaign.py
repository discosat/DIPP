#!/usr/bin/env python3
"""
Run a matrix of experiments unattended and resumably.

    campaign.py matrix.yaml [--outdir runs] [--no-energy]

Stage 5 needs order-100 runs (C4 alone is 8 cells x 5 reps), i.e. hours
unattended. Two properties matter more than speed:

  RESUMABLE -- a run whose directory already holds a complete RUN.json is
  skipped, so a board hang at run 60 of 100 does not cost the first 59.

  HONEST -- a run that could not be verified is recorded as failed rather than
  silently producing a clean-looking null result. An earlier campaign reported
  8/8 from a run where every power cut had failed; that must not be possible
  here.

Matrix:

    run_set: c4-table6
    defaults: {n: 20, images: 1, seed: 43, load: populate}
    runs:
      - {label: decomposed-mmap-low, pipeline: 2, storage: MMAP,
         heuristic: BEST_EFFORT, reps: 5}
"""
import argparse, hashlib, json, os, subprocess, sys, time
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD_DIR = "/home/root/tmp-experiments"

# A campaign runs for hours; block-buffered stdout means no visible progress
# until it ends, and no partial record if it is killed.
sys.stdout.reconfigure(line_buffering=True)


def board(cmd, timeout=300):
    r = subprocess.run([os.path.join(HERE, "board_console.py"), cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def fetch(remote, local, md5sum, timeout=1800):
    r = subprocess.run([os.path.join(HERE, "board_fetch.py"), remote, local, md5sum],
                       capture_output=True, text=True, timeout=timeout)
    return "MATCH" in r.stdout


def remote_md5(path):
    out = board(f"md5sum {path} 2>/dev/null", 300)
    for line in out.splitlines():
        p = line.split()
        if len(p) == 2 and len(p[0]) == 32 and p[1].endswith(os.path.basename(path)):
            return p[0]
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


# ------------------------------------------------------------------ energy
class Energy:
    """Otii recording around a run. Degrades to no-energy rather than failing:
    a wedged instrument must not be able to kill a campaign."""

    def __init__(self, enabled=True):
        self.ok = False
        self.otii = self.dev = self.ctx = None
        if not enabled:
            return
        try:
            from otii_tcp_client import otii_client
            client = otii_client.OtiiClient()
            self.ctx = client.connect(licensing=otii_client.LicensingMode.MANUAL)
            self.otii = self.ctx.__enter__()
            # get_devices() before get_licenses(): the licence call is a remote
            # request that hangs for minutes when the app is unhappy.
            self.dev = self.otii.get_devices()[0]
            self.dev.enable_channel("mc", True)
            self.ok = True
        except Exception as e:
            print(f"  [energy] unavailable ({type(e).__name__}); continuing without")

    def start(self):
        if self.ok:
            try:
                self.otii.get_active_project().start_recording()
            except Exception:
                self.ok = False

    def stop(self):
        if not self.ok:
            return {}
        try:
            proj = self.otii.get_active_project()
            proj.stop_recording()
            rec = proj.get_last_recording()
            info = rec.get_channel_info(self.dev.id, "mc")
            st = rec.get_channel_statistics(self.dev.id, "mc", info["from"], info["to"])
            secs = max(info["to"] - info["from"], 1e-9)
            return {"avg_current_a": st["average"],
                    "avg_power_w": st["average"] * 5.0,
                    "energy_wh": st["average"] * 5.0 * secs / 3600.0,
                    "measured_s": secs}
        except Exception as e:
            print(f"  [energy] read failed: {type(e).__name__}")
            return {}

    def close(self):
        if self.ctx:
            try:
                self.ctx.__exit__(None, None, None)
            except Exception:
                pass


# -------------------------------------------------------------------- board
def board_is_up(timeout, settle=0.0):
    """Poll until Linux answers, or give up.

    The marker is assembled at runtime because the console echoes the command
    before its output. A literal "BOARD_READY" in the command would match its
    own echo -- and a board stranded at the *U-Boot* prompt echoes typed
    characters too, so the naive check reports a healthy Linux on a board that
    has none.
    """
    if settle:
        time.sleep(settle)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if "BOARD_READY" in board("printf 'BOARD_%s\\n' READY", 20):
            board("mountpoint -q /mnt/flash || mount -o ro /dev/sda1 /mnt/flash", 30)
            return True
        time.sleep(10)
    return False


def power_cycle():
    """Last-resort recovery via the Otii, which supplies the board at 5 V.

    The board hangs mid-U-Boot roughly one boot in twenty. Without this, one
    wedge at run 12 of 100 fails every run after it as well -- each one still
    paying the full reboot timeout -- which is a whole overnight campaign lost
    to a fault that a power cut clears.

    SIXTY seconds, not five. After a hard hang (2026-09-14: a classification
    run that never produced a batch, console dead on both ports) 5 s and 10 s
    cuts restored power to a board that drew a normal 3.1 W and never printed
    a byte of U-Boot; a 60 s cut booted it cleanly. Whatever latches in the
    PMIC/SoC outlives a short cut.
    """
    try:
        r = subprocess.run([os.path.join(HERE, "otii_power.py"), "cut", "60"],
                           capture_output=True, text=True, timeout=180)
        print(f"    power cycle: {r.stdout.strip() or r.stderr.strip()}")
        return r.returncode == 0
    except Exception as ex:
        print(f"    power cycle unavailable: {type(ex).__name__}: {ex}")
        return False


def reboot_and_wait(timeout=240):
    """Software reboot: clears memory state and the A8 leak's effects, which is
    what the original experiments did. Never writes to the console during
    U-Boot's countdown -- a keystroke there stops autoboot and strands the board
    at a prompt that looks exactly like a failed boot."""
    try:
        board("sync; reboot", 15)
    except subprocess.TimeoutExpired:
        # An unresponsive console is itself the symptom; fall through to the
        # power cycle rather than aborting the campaign.
        pass
    if board_is_up(timeout, settle=45):   # silent through SPL, U-Boot and early init
        return True
    print("    board did not return from reboot; power-cycling")
    if not power_cycle():
        return False
    return board_is_up(timeout, settle=45)


def run_one(spec, outdir, energy):
    label = spec["label"]
    dest = os.path.join(outdir, label)
    run_json = os.path.join(dest, "RUN.json")
    if os.path.exists(run_json):
        try:
            if json.load(open(run_json)).get("complete"):
                print(f"  {label}: already complete, skipping")
                return "skipped"
        except Exception:
            pass
    os.makedirs(dest, exist_ok=True)

    print(f"  {label}: rebooting")
    if not reboot_and_wait():
        json.dump({"label": label, "complete": False, "error": "board_did_not_return"},
                  open(run_json, "w"), indent=2)
        print(f"  {label}: FAILED (board did not come back)")
        return "failed"

    args = (f"--label {label} --pipeline {spec['pipeline']} --storage {spec['storage']} "
            f"--heuristic {spec['heuristic']} --load {spec.get('load','populate')} "
            f"--n {spec.get('n',20)} --images {spec.get('images',1)} "
            f"--seed {spec.get('seed',43)}")
    if spec.get("image"):
        args += f" --image {spec['image']}"
    if spec.get("keep_cost_cache"):
        args += " --keep-cost-cache"
    if spec.get("trace") is False:
        args += " --no-trace"

    energy.start()
    t0 = time.time()
    board(f"cd {BOARD_DIR} && (setsid ./run.sh {args} > run_{label}.log 2>&1 &) ; echo STARTED", 60)

    remote_board_json = f"{BOARD_DIR}/runs/{label}/board.json"
    budget = 120 + int(spec.get("n", 20)) * 30
    deadline = time.time() + budget
    while time.time() < deadline:
        time.sleep(15)
        # The console echoes the command before its output, so a marker that
        # appears literally in the command matches its own echo and the wait
        # returns immediately -- which then gzips console.log out from under a
        # still-draining run.sh. Assemble the marker at runtime so the literal
        # token exists only in the reply.
        if "BJDONE" in board(
                f"test -f {remote_board_json} && printf 'B%s\\n' JDONE", 30):
            break
    else:
        energy.stop()
        json.dump({"label": label, "complete": False, "error": "run_timeout",
                   "budget_s": budget}, open(run_json, "w"), indent=2)
        print(f"  {label}: FAILED (timeout after {budget}s)")
        return "failed"
    e = energy.stop()
    wall = time.time() - t0

    # Collect. Compress first: these cross a 9.7 KB/s serial link.
    board(f"cd {BOARD_DIR}/runs/{label} && gzip -9 -f console.log trace.json 2>/dev/null; echo GZ", 120)
    got = {}
    for name in ("board.json", "pre.txt", "post.txt", "console.log.gz",
                 "trace.json.gz", "storage.vmem", "predictions.txt", "kept_pixels.txt"):
        remote = f"{BOARD_DIR}/runs/{label}/{name}"
        m = remote_md5(remote)
        if not m:
            continue
        if fetch(remote, os.path.join(dest, name), m):
            got[name] = m

    rec = {"label": label, "wall_s": round(wall, 1), "fetched": sorted(got)}
    bj = os.path.join(dest, "board.json")
    if os.path.exists(bj):
        try:
            rec.update(json.load(open(bj)))
        except Exception as ex:
            rec["board_json_error"] = str(ex)
    rec["energy"] = e
    for f in ("console.log.gz", "trace.json.gz"):
        p = os.path.join(dest, f)
        if os.path.exists(p):
            rec[f"{f}_sha256"] = sha256(p)
    rec["complete"] = bool(rec.get("n_executions")) and "board.json" in got
    json.dump(rec, open(run_json, "w"), indent=2)

    status = "ok" if rec["complete"] else "failed"
    print(f"  {label}: {status}  executions={rec.get('n_executions','?')} "
          f"completed={rec.get('n_completed','?')} "
          f"energy={rec.get('energy',{}).get('energy_wh','n/a')} wall={wall:.0f}s")
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("matrix")
    ap.add_argument("--outdir", default="runs")
    ap.add_argument("--no-energy", action="store_true")
    a = ap.parse_args()

    m = yaml.safe_load(open(a.matrix))
    defaults = m.get("defaults", {})
    specs = []
    for r in m["runs"]:
        base = dict(defaults); base.update(r)
        reps = int(base.pop("reps", 1))
        for i in range(1, reps + 1):
            s = dict(base)
            s["label"] = f"{base['label']}-{i:02d}" if reps > 1 else base["label"]
            specs.append(s)

    outdir = os.path.join(a.outdir, m.get("run_set", "campaign"))
    os.makedirs(outdir, exist_ok=True)
    print(f"=== campaign '{m.get('run_set')}': {len(specs)} runs -> {outdir} ===")

    energy = Energy(enabled=not a.no_energy)
    tally = {"ok": 0, "failed": 0, "skipped": 0}
    try:
        for i, s in enumerate(specs, 1):
            print(f"[{i}/{len(specs)}]")
            try:
                tally[run_one(s, outdir, energy)] += 1
            except Exception as ex:
                tally["failed"] += 1
                print(f"  {s['label']}: EXCEPTION {type(ex).__name__}: {ex}")
    finally:
        energy.close()

    print(f"\n=== {tally['ok']} ok, {tally['failed']} failed, {tally['skipped']} skipped ===")
    return 0 if tally["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
