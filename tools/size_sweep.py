#!/usr/bin/env python3
"""Stage 5a step 4: measure every module's latency (and the run's energy) at
the input sizes the campaigns will actually present.

    size_sweep.py experiments/matrices/size-sweep.yaml [--only LABEL ...] [--no-energy]

Expects the board to hold a set staged from experiments/sweeps/size-sweep/
(the flight config with the monolith pinned per pipeline; identical module
hashes). One result file per run in the matrix's outdir; a run whose file is
complete and valid is skipped, so the sweep resumes after any interruption and
re-runs only what is missing or invalid -- never a whole chain.

Per run:
  1. no daemon, SysV segments reclaimed (tools/shmclean);
  2. idle floor: the previous run's after-floor when nothing happened in
     between, else a fresh one -- read IMMEDIATELY before every run;
  3. run.sh --load populate on one image with --images k frames per batch,
     under the cost-bench setting (campaign.BENCH_RUN_ARGS: the power model
     never gates a cost measurement);
  4. completion polled every 15 s; four silent polls (~1 min of dead
     console after the ~2 min the board normally answers in) is a hard hang:
     the board is power-cycled (60 s cut) and THAT run alone is retried once;
  5. idle floor again, after;
  6. the board reduces its own log to one line per execution (module,
     param_id, n_input, latency, energy debited, ts) before transfer -- a
     whole console.log over the serial console truncates silently.

Validity (the energy-calibration rule): a run whose before/after floors sit
on different sides of the 2.85 W band edge straddled the idle-state
transition and is invalid; it is recorded and re-run on the next invocation.

Energy is the run's whole-board reading. It is the VALIDATION side of the
energy model -- measured energy above the floor against the sum, over the
run's executions, of each stage's mean power x its measured latency
(analysis/cost_model_fit.py) -- which tests the plan's U3 premise directly:
is a stage's mean power flat in its input size?
"""
import argparse, json, os, re, subprocess, sys, time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import campaign  # noqa: E402

BOARD_DIR = campaign.BOARD_DIR
BAND_EDGE_W = 2.85
board = campaign.board
sys.stdout.reconfigure(line_buffering=True)

REQUIRED_SETTINGS = ("storage", "idle_s", "min_drain")
REQUIRED_RUN = ("label", "pipeline", "image", "images", "n")


def load_matrix(path):
    m = yaml.safe_load(open(path))
    s = m.get("settings", {})
    bad = [f"settings lacks {k}" for k in REQUIRED_SETTINGS if k not in s]
    for r in m.get("runs", []):
        bad += [f"{r.get('label', '?')}: lacks {k}" for k in REQUIRED_RUN if k not in r]
    labels = [r.get("label") for r in m.get("runs", [])]
    dup = {l for l in labels if labels.count(l) > 1}
    bad += [f"duplicate label {l}" for l in sorted(dup)]
    if not m.get("runs"):
        bad.append("no runs")
    if bad:
        raise SystemExit("REFUSING the matrix -- nothing has run:\n  " + "\n  ".join(bad))
    return m


def floor(energy, secs):
    """Idle power with the daemon stopped."""
    alive = board("pidof dipp || printf 'NO%s\\n' DAEMON", 60)
    if "NODAEMON" not in alive:
        board("pkill -TERM dipp; sleep 2; pkill -KILL dipp; printf 'K%s\\n' ILLED", 60)
    board(f"cd {BOARD_DIR} && ./shmclean", 60)
    energy.start()
    time.sleep(secs)
    e = energy.stop()
    return e.get("avg_power_w") if e else None


def execs(label):
    """One compact line per EXEC, reduced on the board."""
    out = board(
        f"grep -a '^EXEC ' {BOARD_DIR}/runs/{label}/console.log | awk '{{"
        f"for(i=1;i<=NF;i++){{split($i,a,\"=\"); v[a[1]]=a[2]}} "
        f"print \"X\", v[\"ts_us\"], v[\"module\"], v[\"param_id\"], v[\"num_images\"], "
        f"v[\"n_input\"], v[\"latency_meas_us\"], v[\"energy_debited_uwh\"], v[\"uuid\"]}}'", 600)
    rows = []
    for ln in out.splitlines():
        f = ln.split()
        if len(f) == 9 and f[0] == "X" and f[1].isdigit():
            rows.append({"ts_us": int(f[1]), "module": f[2], "param_id": int(f[3]),
                         "num_images": int(f[4]), "n_input": int(f[5]),
                         "latency_us": int(f[6]), "energy_debited_uwh": float(f[7]),
                         "uuid": f[8]})
    return rows


def run_board(label, spec, settings):
    board(f"cd {BOARD_DIR} && rm -rf runs/{label}", 60)
    args = (f"--label {label} --pipeline {spec['pipeline']} --storage {settings['storage']} "
            f"--heuristic BEST_EFFORT --load populate --n {spec['n']} --images {spec['images']} "
            f"--image {spec['image']} --no-trace --min-drain {settings['min_drain']} "
            f"{campaign.BENCH_RUN_ARGS}")
    board(f"cd {BOARD_DIR} && (setsid ./run.sh {args} > run_{label}.log 2>&1 &) ; "
          f"printf 'ST%s\\n' ARTED", 60)
    budget = 300 + int(spec["n"]) * int(spec["images"]) * 40 + 3 * int(settings["min_drain"])
    deadline, silent = time.time() + budget, 0
    while time.time() < deadline:
        time.sleep(15)
        try:
            r = board(f"test -f {BOARD_DIR}/runs/{label}/board.json && printf 'B%s\\n' JDONE", 30)
            silent = 0 if r.strip() else silent + 1
        except subprocess.TimeoutExpired:
            r, silent = "", silent + 1
        if "BJDONE" in r:
            return "done"
        if silent >= 4:
            return "hang"
    return "timeout"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("matrix")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--no-energy", action="store_true",
                    help="latency only; without it, an unavailable Otii stops the sweep")
    a = ap.parse_args()

    m = load_matrix(a.matrix)
    s = m["settings"]
    outdir = m.get("outdir", f"analysis/{m.get('run_set', 'size-sweep')}")
    os.makedirs(outdir, exist_ok=True)

    energy = campaign.Energy(enabled=not a.no_energy)
    if not a.no_energy and not energy.ok:
        raise SystemExit("Otii unavailable: refusing an energy sweep (--no-energy for latency only)")

    todo = []
    for r in m["runs"]:
        if a.only and r["label"] not in a.only:
            continue
        p = os.path.join(outdir, f"{r['label']}.json")
        if os.path.exists(p):
            try:
                prev = json.load(open(p))
                if prev.get("complete") and prev.get("valid", True):
                    continue
            except Exception:
                pass
        todo.append(r)
    print(f"=== size sweep: {len(todo)} of {len(m['runs'])} runs to do -> {outdir} ===")

    floor_w = None          # last after-floor, valid only if nothing ran since
    tally = {"ok": 0, "invalid": 0, "failed": 0}
    try:
        for i, r in enumerate(todo, 1):
            label = f"sweep-{r['label']}"
            print(f"[{i}/{len(todo)}] {r['label']}: pipeline {r['pipeline']} k={r['images']} "
                  f"n={r['n']} {r['image']}")
            for attempt in (1, 2):
                before = floor_w if floor_w is not None else (
                    floor(energy, s["idle_s"]) if not a.no_energy else None)
                floor_w = None
                energy.start()
                t0 = time.time()
                status = run_board(label, r, s)
                e = energy.stop()
                wall = time.time() - t0
                if status == "hang":
                    print(f"  hard hang (console silent ~1 min); power-cycling and retrying this run")
                    campaign.power_cycle()
                    campaign.board_is_up(300)
                    continue
                break
            rec = {"label": r["label"], "spec": r, "settings": s, "status": status,
                   "wall_s": round(wall, 1), "energy": e, "floor_before_w": before}
            if status == "done":
                after = floor(energy, s["idle_s"]) if not a.no_energy else None
                floor_w = after
                rec["floor_after_w"] = after
                rec["execs"] = execs(label)
                bj = board(f"cat {BOARD_DIR}/runs/{label}/board.json", 120)
                for key in ("n_executions", "n_completed", "build_id", "duration_s"):
                    mm = re.search(rf'"{key}": "?([^",\n]*)"?', bj)
                    if mm:
                        rec[key] = mm.group(1)
                rec["complete"] = bool(rec["execs"])
                if before is not None and after is not None:
                    rec["valid"] = (before < BAND_EDGE_W) == (after < BAND_EDGE_W)
                    rec["floor_shift_w"] = round(after - before, 4)
            else:
                rec["complete"] = False
            json.dump(rec, open(os.path.join(outdir, f"{r['label']}.json"), "w"), indent=1)
            if not rec["complete"]:
                tally["failed"] += 1
                print(f"  FAILED ({status})")
            elif not rec.get("valid", True):
                tally["invalid"] += 1
                print(f"  INVALID: floor {before:.3f} -> {after:.3f} W crosses {BAND_EDGE_W} W; "
                      f"re-run it by invoking the sweep again")
            else:
                tally["ok"] += 1
                lat = [x["latency_us"] for x in rec["execs"]]
                print(f"  ok: {len(rec['execs'])} executions, {wall:.0f} s"
                      + (f", {e['energy_wh'] * 1e3:.2f} mWh, floors {before:.3f}/{after:.3f} W"
                         if e and before and after else ""))
    finally:
        energy.close()
    print(f"\n=== {tally['ok']} ok, {tally['invalid']} invalid, {tally['failed']} failed ===")
    return 0 if tally["failed"] == 0 and tally["invalid"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
