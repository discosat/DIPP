#!/usr/bin/env python3
"""
C3 with REAL power cuts, using the Otii Ace as the board's 5 V supply.

    c3_powercut.py [--trials 20] [--batches 20] [--pipeline 2]

This is the test SIGKILL cannot do. A killed process still leaves its mmap
writes in the kernel page cache, so the next process sees them whether or not
msync ran -- SIGKILL cannot produce a torn write. Only losing power mid-msync
can, which is what decides whether the queue needs shadow paging.

After every cut the board cold-boots, so each cycle must: wait for the login
prompt, log in, re-mount /mnt/flash (not mounted at boot), and restart the
daemon. The daemon log accumulates across boots so batches can be tracked by
UUID from end to end.

Power is ALWAYS restored on exit, including on exception or Ctrl-C.
"""
import os, re, sys, time, termios, select, argparse, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_tty
# Resolved when a Console is opened, not at import: resolving probes every
# /dev/ttyUSB* with a CR, which must never happen while a bench run holds the
# console (2026-10-07: a --help run probed it mid-campaign).
DIR = "/home/root/tmp-experiments"
IMG = "/mnt/flash/cs12-frames/cs12_test_matched_00.bayerRG"   # the device frames are retired
LOG = "pc.log"
DAEMON_ENV = "STORAGE_MODE=MMAP "
# The battery simulator has no defaults (since 2026-09-28): the platform file
# is sourced into the daemon's environment, with the initial conditions.
PLATFORM_FILE = SOC0 = PHASE0 = None
HERE = os.path.dirname(os.path.abspath(__file__))


class Console:
    def __init__(self):
        self.fd = os.open(board_tty.resolve(), os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        a = termios.tcgetattr(self.fd)
        a[0] = a[1] = a[3] = 0
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        a[4] = a[5] = termios.B115200
        a[6] = list(a[6]); a[6][termios.VMIN] = 0; a[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, a)

    def drain(self, secs):
        out = b""; end = time.time() + secs
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.2)
            if r:
                try: out += os.read(self.fd, 8192)
                except BlockingIOError: pass
        return out.decode("utf-8", "replace")

    def send(self, s):
        os.write(self.fd, (s + "\r").encode())   # bare CR: \r\n double-submits

    def cmd(self, s, wait=3):
        self.send(s)
        return self.drain(wait)

    def wait_for_login(self, timeout=180, quiet_first=12.0):
        """Boot to a usable root shell. Returns True on success.

        Two phases, because the two hazards pull in opposite directions:

        1. Stay SILENT for `quiet_first` seconds. Any keypress during U-Boot's
           autoboot countdown stops it and strands the board at the U-Boot
           prompt -- which then looks exactly like a board that failed to boot.
           This phase also catches the login prompt on a normal boot.
        2. Only then poke with a bare CR. Needed because a board already sitting
           idle at a prompt emits nothing at all, so passive reading alone never
           sees it. By now any countdown has long expired.
        """
        end = time.time() + timeout
        buf = self.drain(quiet_first)

        while time.time() < end:
            tail = buf[-400:]
            if "PicoCore" in tail and "#" in tail:
                self.send("boot")          # stranded in U-Boot: resume
                buf = self.drain(20)
                continue
            if "login:" in tail:
                self.send("root")
                out = self.drain(5)
                if "#" in out or "root@" in out:
                    return True
                buf = out
                continue
            if "root@" in tail or re.search(r"#\s*$", tail):
                return True
            self.send("")                  # safe now: countdown is long over
            buf = self.drain(3)
        return False


def power(action, seconds=None):
    cmd = [os.path.join(HERE, "otii_power.py"), action]
    if seconds is not None:
        cmd.append(str(seconds))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180)


def boot_and_prepare(con, start_daemon=True, times=None):
    ok = _boot_and_prepare(con, start_daemon, times)
    if ok:
        install_snapshot(con)
    return ok


def _boot_and_prepare(con, start_daemon=True, times=None):
    if not con.wait_for_login():
        return False
    if times is not None:
        times["login"] = time.monotonic()
    con.cmd(f"cd {DIR}", 2)
    con.cmd("mountpoint -q /mnt/flash || mount -o ro /dev/sda1 /mnt/flash", 4)
    if start_daemon:
        # Verify the daemon actually came up. A previous campaign logged only 14
        # daemon starts across 20 cuts, so several cuts landed on an idle board
        # -- which silently weakens a negative result, because a cut that hits
        # nothing cannot corrupt anything.
        for attempt in range(2):
            con.cmd(f"cd {DIR} && sh pc_start.sh", 4)
            if "DAEMON_UP" in con.cmd("pidof dipp > /dev/null && echo DAEMON_UP || echo DAEMON_DOWN", 4):
                if times is not None:
                    times["daemon"] = time.monotonic()
                return True
        print("  WARNING: daemon did not start this cycle", flush=True)
        return False
    return True


SNAP_SH = r"""#!/bin/sh
# Evidence snapshot, read over the console BEFORE every cut (the log on the
# board is not durable under a power cut). One tag per line.
cd /home/root/tmp-experiments
grep -h 'Image sent' pc_fed.txt 2>/dev/null | sed 's/.*uuid=\([0-9a-f-]*\).*/F \1/'
sed 's/ .*//; s/^/C /' /usr/share/dipp/completed.log 2>/dev/null
grep -a '^STARTUP_QUEUES' pc.log | sed 's/^/S /'
grep -a '^CHECKPOINT ' pc.log | sed 's/^CHECKPOINT uuid=\([0-9a-f-]*\) progress=\([0-9]*\).*/K \1 \2/'
grep -a '^HYBRID_LOAD' pc.log | sed 's/^HYBRID_LOAD uuid=\([0-9a-f-]*\) progress=\(-*[0-9]*\).*/L \1 \2/'
echo "Q $(grep -ac '^QUARANTINED' pc.log)"
echo "B $(grep -ac 'SIPP build' pc.log)"
echo SNAPEND
"""


def install_snapshot(con):
    """Write the snapshot script to the board line by line: the console
    cannot take a 600-character command in one write."""
    con.cmd("rm -f /tmp/snap.sh", 1)
    for ln in SNAP_SH.strip("\n").splitlines():
        con.cmd("echo '" + ln.replace("'", "'\\''") + "' >> /tmp/snap.sh", 0.4)
    con.cmd("chmod +x /tmp/snap.sh; wc -l /tmp/snap.sh", 2)


def snapshot(con, acc):
    """Fold the board's current evidence into the host-side accumulators."""
    out = ""
    for _ in range(3):
        out = con.cmd("sh /tmp/snap.sh", 15).replace("\r", "")
        if "SNAPEND" in out:
            break
    for ln in out.splitlines():
        parts = ln.strip().split()
        if not parts:
            continue
        tag = parts[0]
        if tag == "F" and len(parts) == 2 and len(parts[1]) == 36:
            acc["fed"].add(parts[1])
        elif tag == "C" and len(parts) == 2 and len(parts[1]) == 36:
            acc["completed"].setdefault(parts[1], 0)
        elif tag == "S" and len(parts) >= 3:
            acc["startups"].add(" ".join(parts[1:]) + f" @{acc['boot_tag']}")
        elif tag == "K" and len(parts) == 3 and len(parts[1]) == 36:
            acc["checkpoints"].add((parts[1], parts[2]))
        elif tag == "L" and len(parts) == 3 and len(parts[1]) == 36:
            acc["loads"].add((parts[1], parts[2], acc["boot_tag"]))
    comp = [ln.split()[1] for ln in out.splitlines() if ln.startswith("C ") and len(ln.split()) == 2]
    for u in set(comp):
        acc["completed"][u] = max(acc["completed"].get(u, 0), comp.count(u))


RESUME_AWK = ("awk '/SIPP build/{s=0;e=0} /^(QUEUE_SELECT|HYBRID_LOAD) /{s++} /^EXEC /{e++} "
              "END{print \"RESUME\", s+0, e+0}' " + LOG)


def time_recovery(con, cycle, t_on, times, timeout=120):
    """Seconds from power restored to: login prompt, daemon running, first
    queue selection after this boot (processing resumed), first module
    completed. The console round trip (~1 s) bounds the resolution. A boot
    with nothing queued never resumes; that is printed as idle, not as a time."""
    sel = ex = None
    end = time.monotonic() + timeout
    while time.monotonic() < end and ex is None:
        out = con.cmd(f"cd {DIR} && {RESUME_AWK}", 2)
        m = re.findall(r"RESUME (\d+) (\d+)", out)
        if m:
            s_, e_ = (int(x) for x in m[-1])
            now = time.monotonic()
            if s_ and sel is None:
                sel = now
            if e_:
                ex = now
    f = lambda t: f"{t - t_on:.1f}" if t is not None else "none"
    print(f"RECOVERY cycle={cycle} login_s={f(times.get('login'))} daemon_s={f(times.get('daemon'))} "
          f"first_select_s={f(sel)} first_exec_s={f(ex)}" + ("" if sel else " (idle or no work within timeout)"),
          flush=True)


def main():
    global IMG, DAEMON_ENV, PLATFORM_FILE, SOC0, PHASE0
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--batches", type=int, default=20)
    ap.add_argument("--pipeline", type=int, default=2)
    ap.add_argument("--settle", type=float, default=12.0,
                    help="seconds of processing between cuts")
    ap.add_argument("--storage", default="MMAP", choices=["MMAP", "MEM", "HYBRID"])
    ap.add_argument("--env", default="", help="extra daemon environment, e.g. 'DIPP_HALT_AFTER_MODULE=3'")
    ap.add_argument("--image", default=IMG)
    ap.add_argument("--cut", type=float, default=10.0, help="seconds of power cut")
    ap.add_argument("--feed-wait", type=float, default=8.0,
                    help="seconds between starting the feed and the first cut")
    ap.add_argument("--platform", required=True, help="platform .env on the board, e.g. disco2-bol.env")
    ap.add_argument("--soc0", required=True)
    ap.add_argument("--phase0", required=True)
    ap.add_argument("--sched", required=True,
                    help="scheduling env, e.g. 'DIPP_SCHED_Q_MED=12 DIPP_SCHED_Q_LOW=20 "
                         "DIPP_SCHED_GRANULARITY=stage DIPP_SCHED_GATE_MED_MS=5000 DIPP_SCHED_GATE_LOW_MS=3000'")
    ap.add_argument("--slo-pattern", required=True,
                    help="comma list of per-frame SLOs (s) cycled over the feed, e.g. 300,300,20")
    ap.add_argument("--feed-gap", type=float, required=True,
                    help="seconds between frames (0 = all at once); frames that arrive while a batch "
                         "runs are what make stage-boundary preemption happen")
    ap.add_argument("--time-recovery", action="store_true",
                    help="after each boot, poll the console until processing resumes and print a "
                         "RECOVERY line (s from power restored to login, daemon up, first queue "
                         "selection, first module completed); adds polling time to every cycle")
    a = ap.parse_args()
    IMG = a.image
    DAEMON_ENV = f"STORAGE_MODE={a.storage} {a.sched} {a.env} ".rstrip() + " "
    PLATFORM_FILE, SOC0, PHASE0 = a.platform, a.soc0, a.phase0
    slos = [int(x) for x in a.slo_pattern.split(",")]
    print(f"=== platform {a.platform} soc0 {a.soc0} phase0 {a.phase0}; {a.sched}; "
          f"SLO pattern {slos}; feed gap {a.feed_gap} s; image {IMG} ===", flush=True)

    con = Console()
    try:
        print("=== ensuring power on and board booted ===", flush=True)
        power("on"); time.sleep(2)
        if not boot_and_prepare(con, start_daemon=False):
            print("FAILED to reach a shell"); return 1

        print("=== clearing state ===", flush=True)
        con.cmd("pkill -KILL dipp", 2)
        con.cmd("rm -f /usr/share/dipp/queue_file /usr/share/dipp/partially_processed_queue_file "
                "/usr/share/dipp/cost.cache", 3)
        con.cmd("find /usr/share/dipp/data -type f -exec rm -f {} +", 5)
        con.cmd(f"cd {DIR} && rm -f {LOG} pc_fed.txt /usr/share/dipp/completed.log", 2)
        # Start from the fitted cost model, as the campaigns do (stage.sh cost.seed).
        con.cmd(f"cd {DIR} && cp cost.seed /usr/share/dipp/cost.cache && echo SEEDED", 3)

        # The daemon start line (platform + scheduling env) is long; the console
        # cannot take it in one write, so it goes into a script on the eMMC
        # (/tmp does not survive a power cut), written line by line once.
        start = ["#!/bin/sh", f"cd {DIR}", "set -a", f". ./{PLATFORM_FILE}", "set +a",
                 f"export DIPP_BATT_SOC0={SOC0} DIPP_BATT_PHASE0={PHASE0}"]
        start += [f"export {kv}" for kv in DAEMON_ENV.split()]
        start.append(f"setsid ./dipp -i CAN -p can0 -a 5423 >> {LOG} 2>&1 &")
        con.cmd(f"cd {DIR} && rm -f pc_start.sh", 1)
        for ln in start:
            con.cmd(f"echo '{ln}' >> {DIR}/pc_start.sh", 0.4)
        print(con.cmd(f"cat {DIR}/pc_start.sh", 2)[-600:], flush=True)
        con.cmd(f"cd {DIR} && sh pc_start.sh", 4)
        time.sleep(6)
        print(con.cmd(f"grep -a -m2 -E '^SCHED_CONFIG|^BATTERY_CONFIG' {DIR}/{LOG} | cut -c1-160", 3)[-400:], flush=True)

        print(f"=== feeding {a.batches} batches ===", flush=True)
        # Feed in the background and come back after a few seconds: waiting
        # 90 s here let every batch complete before the first cut, so every
        # boot found empty queues and the trial tested nothing (2026-09-21).
        feed = ["#!/bin/sh", f"cd {DIR}"]
        for i in range(a.batches):
            feed.append(f"./emulate_camera 1 {a.pipeline} 123 {IMG} {slos[i % len(slos)]} 2>/dev/null "
                        f"| grep '^Image sent' >> pc_fed.txt")
            if a.feed_gap > 0 and i + 1 < a.batches:
                feed.append(f"sleep {a.feed_gap}")
        feed.append("echo FED >> pc_fed.txt")
        con.cmd("rm -f /tmp/feed.sh", 1)
        for ln in feed:
            con.cmd("echo '" + ln.replace("'", "'\\''") + "' >> /tmp/feed.sh", 0.4)
        con.cmd("chmod +x /tmp/feed.sh; (setsid sh /tmp/feed.sh > /dev/null 2>&1 &)", 2)
        for _ in range(int(a.batches * max(a.feed_gap, 0.5)) + 60):
            if "FEDOK" in con.cmd("grep -q FED " + f"{DIR}/pc_fed.txt && echo FED%s | sed s/%s/OK/", 2):
                break
            time.sleep(1)
        time.sleep(a.feed_wait)

        acc = dict(fed=set(), completed={}, startups=set(), checkpoints=set(), loads=set(), boot_tag=0)
        daemon_failures = 0
        for t in range(1, a.trials + 1):
            time.sleep(a.settle)
            acc["boot_tag"] = t - 1
            snapshot(con, acc)
            print(f"--- cycle {t}/{a.trials}: cutting power (fed {len(acc['fed'])}, completed {len(acc['completed'])}) ---", flush=True)
            r = power("cut", a.cut)
            t_on = time.monotonic()                  # otii_power.py cut returns once power is back
            times = {}
            if r.returncode != 0:
                print("  otii error:", r.stderr[:200])
            if not boot_and_prepare(con, times=times):
                # A board that does not come back needs the long cut
                # (2026-09-14: 5 and 10 s left it drawing 3.1 W with no U-Boot).
                print(f"  cycle {t}: no shell after the cut; 60 s cut and retry", flush=True)
                power("cut", 60)
                t_on, times = time.monotonic(), {}
                if not boot_and_prepare(con, times=times):
                    daemon_failures += 1
                    print(f"  cycle {t}: board back but daemon not confirmed", flush=True)
            if a.time_recovery and "daemon" in times:
                time_recovery(con, t, t_on, times)

        print("=== draining ===", flush=True)
        last, stable = -1, 0
        while stable < 5:
            time.sleep(10)
            out = con.cmd(f"cd {DIR} && grep -c 'Pipeline fully executed' {LOG}", 5)
            m = re.findall(r"(?<![\w=])(\d+)\s*$", out.replace("\r", ""), re.M)
            now = int(m[-1]) if m else last
            stable = stable + 1 if now == last else 0
            last = now
            print(f"  completions={now}", flush=True)

        acc["boot_tag"] = a.trials
        snapshot(con, acc)
        fed, comp = acc["fed"], acc["completed"]
        lost = sorted(u for u in fed if u not in comp)
        dups = sorted(u for u, n in comp.items() if n > 1)
        resumed = sorted(l for l in acc["loads"] if l[1] != "-1")
        print(f"=== RESULT, host-side tally (daemon-start failures: {daemon_failures}) ===", flush=True)
        print(f"fed={len(fed)} completed={len([u for u in comp if u in fed])} lost={len(lost)} "
              f"duplicates={len(dups)} checkpoints={len(acc['checkpoints'])} "
              f"loads_from_ingest={len([l for l in acc['loads'] if l[1] == '-1'])} "
              f"resumed_from_checkpoint={len(resumed)} boots_seen={len(acc['startups'])}")
        for st in sorted(acc["startups"], key=lambda x: int(x.split('@')[1])):
            print("  startup:", st)
        if lost:
            print("  LOST:", lost)
        if dups:
            print("  DUPLICATES:", dups)
        print("=== board-side tally (the log is NOT durable under cuts; for reference only) ===", flush=True)
        script = (
            f"cd {DIR}; "
            f"grep uuid= pc_fed.txt | sed 's/.*uuid=//; s/ .*//' | sort -u > /tmp/f; "
            f"grep 'Pipeline fully executed' {LOG} | sed 's/.*uuid=//; s/ .*//' | sort -u > /tmp/d; "
            f"grep -E '^QUEUE_SELECT|^EXEC ' {LOG} > /tmp/q; "
            f"echo fed=$(grep -c . /tmp/f); echo completed=$(grep -c . /tmp/d); "
            f"nq=0; il=0; for u in $(cat /tmp/f); do "
            f"if grep -q \"$u\" /tmp/q; then grep -q \"^$u$\" /tmp/d || il=$((il+1)); "
            f"else nq=$((nq+1)); fi; done; "
            f"echo never_reached_queue=$nq; echo IN_SCOPE_LOST=$il; "
            f"echo duplicates=$(( $(grep -c 'Pipeline fully executed' {LOG}) - $(grep -c . /tmp/d) )); "
            f"echo QUEUE_CORRUPT=$(grep -c QUEUE_CORRUPT {LOG}); "
            f"echo boots=$(grep -c 'SIPP build' {LOG}); "
            f"echo startup_queues=$(grep '^STARTUP_QUEUES' {LOG} | sed 's/STARTUP_QUEUES //' | tr '\\n' ' '); "
            f"echo resumed_from_checkpoint=$(grep -c '^HYBRID_LOAD.*progress=[0-9]' {LOG}); "
            f"echo checkpoints=$(grep -c '^CHECKPOINT ' {LOG}); echo hybrid_loads=$(grep -c '^HYBRID_LOAD ' {LOG}); "
            f"echo test_halts=$(grep -c '^TEST_HALT' {LOG}); echo quarantined=$(grep -c '^QUARANTINED' {LOG}); "
            f"echo preemptions=$(grep -c '^PREEMPT ' {LOG}); echo sched_config=$(grep -m1 '^SCHED_CONFIG' {LOG} | cut -c1-80); "
            f"echo orphans_removed=$(grep -c '^ORPHAN_REMOVE' {LOG}); "
            f"echo segments_now=$(( $(wc -l < /proc/sysvipc/shm) - 1 )); echo files_now=$(ls /usr/share/dipp/data 2>/dev/null | wc -l)"
        )
        print(con.cmd(script, 30))
        return 0
    finally:
        print("=== restoring power ===", flush=True)
        power("on")


if __name__ == "__main__":
    sys.exit(main())
