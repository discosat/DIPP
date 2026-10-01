#!/usr/bin/env python3
"""A8 fault-injection run: one batch through demosaic -> tile -> crash_and_burn
for every fail_case, checking that the daemon survives each and does what
A8 says it does.

    tools/fault_inject.py [--cases 0 1 ...] [-o analysis/stage5/fault_inject.json]

For each case the board runs one batch (run.sh, pipeline 2 rewritten as
demosaic, tile, crash_and_burn(fail_case)); the expected sequence in the
console log is: the module dies (MODULE_EXIT_CRASH / _CUSTOM / _TIMEOUT
recorded via 'Error: NNNNNN'), QUARANTINE_FAILURE failures=1, RETRY at
lowest effort, the module dies again, QUARANTINE_FAILURE failures=2,
QUARANTINED with a record, QUEUE_REMOVE reason=quarantined -- and the daemon
is still alive at the end (run.sh stops it; 'pidof' is checked before the
stop). After each case: no leaked SysV segment, no orphan batch file, and the
quarantine record exists.
"""
import argparse, json, os, re, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import campaign  # noqa: E402  (BENCH_RUN_ARGS)
REPO = os.path.dirname(HERE)
FLIGHT = os.path.join(REPO, "experiments", "board", "flight-config")
BOARD_DIR = "/home/root/tmp-experiments"
CASES = ["arithmetic", "null_deref", "protected_page", "write_rodata", "stack_overflow",
         "memcpy_null", "index_oob", "null_funcptr", "malloc_1tb", "double_free",
         "buffer_overflow", "shm_grow", "hang"]
PIPE = """pipeline:
- name: libdemosaic
  implementations:
  - param_id: 1
    effort_level: default
- name: libtile
  implementations:
  - param_id: 2
    effort_level: default
- name: libcrash_and_burn
  implementations:
  - param_id: 15
    effort_level: default
"""


def board(cmd, timeout=300):
    r = subprocess.run([os.path.join(HERE, "board_console.py"), f"( {cmd} ) < /dev/null"],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def make_vmem(case, out):
    import shutil
    tmp = tempfile.mkdtemp(prefix="fault-")
    for f in os.listdir(FLIGHT):
        if f.endswith(".yaml"):
            shutil.copy(os.path.join(FLIGHT, f), tmp)
    open(os.path.join(tmp, "pipeline_2.yaml"), "w").write(PIPE)
    m15 = open(os.path.join(tmp, "module_15.yaml")).read()
    m15 = re.sub(r"(- key: fail_case\n  type: 3\n  value: )\d+", rf"\g<1>{case}", m15)
    open(os.path.join(tmp, "module_15.yaml"), "w").write(m15)
    subprocess.run([sys.executable, os.path.join(HERE, "make_storage_vmem.py"), "build",
                    os.path.join(tmp, "manifest.yaml"), "-o", out], check=True, capture_output=True)


def push(local, remote):
    for _ in range(3):
        r = subprocess.run([os.path.join(HERE, "board_push.py"), local, remote], capture_output=True, timeout=900)
        if r.returncode == 0:
            return True
        time.sleep(5)
    return False


def run_case(case, image, drain):
    label = f"fault-{case:02d}-{CASES[case]}"
    vmem = os.path.join(tempfile.gettempdir(), "fault.vmem")
    make_vmem(case, vmem)
    if not push(vmem, f"{BOARD_DIR}/storage.vmem"):
        return dict(case=case, name=CASES[case], error="vmem push failed")
    board(f"cd {BOARD_DIR} && ./shmclean; rm -rf /usr/share/dipp/quarantine; rm -f /usr/share/dipp/data/batch_*", 60)
    board(f"cd {BOARD_DIR} && (setsid ./run.sh --label {label} --pipeline 2 --storage MEM "
          f"--heuristic BEST_EFFORT --load populate --n 1 --images 1 --no-trace "
          f"--min-drain {drain} --image {image} {campaign.BENCH_RUN_ARGS} > run_{label}.log 2>&1 &); printf 'ST%s\\n' ARTED", 60)
    deadline = time.time() + 240 + drain * 3 + (120 if case == 12 else 0)
    alive_during = None
    while time.time() < deadline:
        time.sleep(10)
        try:
            out = board(f"test -f {BOARD_DIR}/runs/{label}/board.json && printf 'B%s\\n' JDONE; "
                        f"pidof dipp >/dev/null && printf 'AL%s\\n' IVE", 30)
        except subprocess.TimeoutExpired:
            out = ""
        if "ALIVE" in out:
            alive_during = True
        if "BJDONE" in out:
            break
    log = board(f"grep -aE 'Error: |QUARANTINE|RETRY|QUEUE_REMOVE|MODULE_RESULT_INVALID|Module timeout|"
                f"Child process|fully executed|Pipeline execution returned' {BOARD_DIR}/runs/{label}/console.log | head -n 60", 120)
    lines = [ln.rstrip() for ln in log.splitlines() if ln.strip() and not ln.startswith("root@")]
    post = board(f"echo SEG=$(( $(wc -l < /proc/sysvipc/shm) - 1 )); echo FILES=$(ls /usr/share/dipp/data/batch_* 2>/dev/null | wc -l); "
                 f"echo QREC=$(ls /usr/share/dipp/quarantine/*.txt 2>/dev/null | wc -l); cat /usr/share/dipp/quarantine/*.txt 2>/dev/null | head -n 20", 60)
    m = {k: int(v) for k, v in re.findall(r"(SEG|FILES|QREC)=(\d+)", post)}
    errs = re.findall(r"Error: (\d+)", log)
    rec = dict(case=case, name=CASES[case], errors=errs,
               failures=len(re.findall(r"QUARANTINE_FAILURE", log)),
               retried=bool(re.search(r"RETRY uuid", log)),
               quarantined=bool(re.search(r"^QUARANTINED ", log, re.M)),
               removed=bool(re.search(r"reason=quarantined", log)),
               daemon_alive_during=alive_during, leaked_segments=m.get("SEG"), leaked_files=m.get("FILES"),
               records=m.get("QREC"), log=lines)
    if case == 11:
        # shm_grow is not a fault: the module doubles its output and the
        # STORAGE_MEM resize path must absorb it. Expected: the batch
        # completes, nothing is quarantined, nothing leaks.
        completed = any("fully executed" in ln for ln in lines)
        ok = completed and rec["failures"] == 0 and rec["leaked_segments"] == 0 and rec["leaked_files"] == 0
        rec["completed"] = completed
    else:
        ok = rec["failures"] == 2 and rec["retried"] and rec["quarantined"] and rec["removed"] \
            and rec["leaked_segments"] == 0 and rec["leaked_files"] == 0 and rec["records"] == 1
    rec["ok"] = ok
    print(f"case {case:2} {CASES[case]:16} errors={','.join(errs[:4]):24} failures={rec['failures']} retry={rec['retried']} "
          f"quarantined={rec['quarantined']} seg={rec['leaked_segments']} files={rec['leaked_files']} rec={rec['records']}  {'OK' if ok else 'FAIL'}", flush=True)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="+", type=int, default=list(range(len(CASES))))
    ap.add_argument("--image", default="cs12_test_matched_00.bayerRG")
    ap.add_argument("--drain", type=int, default=30)
    ap.add_argument("-o", "--out", default=os.path.join(REPO, "analysis", "stage5", "fault_inject.json"))
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    results = json.load(open(a.out)) if os.path.exists(a.out) else {}
    for c in a.cases:
        # the hang case fails only by timeout: two 30 s timeouts (first run and
        # the retry) must pass before the drain may end the run
        results[CASES[c]] = run_case(c, a.image, max(a.drain, 100) if c == 12 else a.drain)
        json.dump(results, open(a.out, "w"), indent=1)
    n_ok = sum(1 for r in results.values() if r.get("ok"))
    print(f"\n{n_ok}/{len(results)} cases OK; wrote {a.out}")


if __name__ == "__main__":
    sys.exit(main())
