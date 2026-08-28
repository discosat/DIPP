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

DEV = "/dev/ttyUSB0"
DIR = "/home/root/tmp-experiments"
IMG = "/mnt/flash/2025-11-10/sentinel2-dataset/amager-no-clouds.bayerRG"
LOG = "pc.log"
HERE = os.path.dirname(os.path.abspath(__file__))


class Console:
    def __init__(self):
        self.fd = os.open(DEV, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
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


def boot_and_prepare(con, start_daemon=True):
    if not con.wait_for_login():
        return False
    con.cmd(f"cd {DIR}", 2)
    con.cmd("mountpoint -q /mnt/flash || mount -o ro /dev/sda1 /mnt/flash", 4)
    if start_daemon:
        # Verify the daemon actually came up. A previous campaign logged only 14
        # daemon starts across 20 cuts, so several cuts landed on an idle board
        # -- which silently weakens a negative result, because a cut that hits
        # nothing cannot corrupt anything.
        for attempt in range(2):
            con.cmd(f"cd {DIR} && (setsid ./dipp -i CAN -p can0 -a 5423 >> {LOG} 2>&1 &)", 4)
            if "DAEMON_UP" in con.cmd("pidof dipp > /dev/null && echo DAEMON_UP || echo DAEMON_DOWN", 4):
                return True
        print("  WARNING: daemon did not start this cycle", flush=True)
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--batches", type=int, default=20)
    ap.add_argument("--pipeline", type=int, default=2)
    ap.add_argument("--settle", type=float, default=12.0,
                    help="seconds of processing between cuts")
    a = ap.parse_args()

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
        con.cmd(f"cd {DIR} && rm -f {LOG} pc_fed.txt", 2)

        con.cmd(f"cd {DIR} && (setsid ./dipp -i CAN -p can0 -a 5423 >> {LOG} 2>&1 &)", 4)
        time.sleep(6)

        print(f"=== feeding {a.batches} batches ===", flush=True)
        con.cmd(f"cd {DIR} && for i in $(seq 1 {a.batches}); do "
                f"./emulate_camera 1 {a.pipeline} 123 {IMG} 2>/dev/null "
                f"| grep '^Image sent' >> pc_fed.txt; done; echo FED", 90)

        daemon_failures = 0
        for t in range(1, a.trials + 1):
            time.sleep(a.settle)
            print(f"--- cycle {t}/{a.trials}: cutting power ---", flush=True)
            r = power("cut", 4)
            if r.returncode != 0:
                print("  otii error:", r.stderr[:200])
            if not boot_and_prepare(con):
                daemon_failures += 1
                print(f"  cycle {t}: board back but daemon not confirmed", flush=True)

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

        print(f"=== RESULT (daemon-start failures: {daemon_failures}) ===", flush=True)
        script = (
            f"cd {DIR}; "
            f"sed 's/.*uuid=//; s/ .*//' pc_fed.txt | sort -u > /tmp/f; "
            f"grep 'Pipeline fully executed' {LOG} | sed 's/.*uuid=//; s/ .*//' | sort -u > /tmp/d; "
            f"grep -E '^QUEUE_SELECT|^EXEC ' {LOG} > /tmp/q; "
            f"echo fed=$(grep -c . /tmp/f); echo completed=$(grep -c . /tmp/d); "
            f"nq=0; il=0; for u in $(cat /tmp/f); do "
            f"if grep -q \"$u\" /tmp/q; then grep -q \"^$u$\" /tmp/d || il=$((il+1)); "
            f"else nq=$((nq+1)); fi; done; "
            f"echo never_reached_queue=$nq; echo IN_SCOPE_LOST=$il; "
            f"echo duplicates=$(( $(grep -c 'Pipeline fully executed' {LOG}) - $(grep -c . /tmp/d) )); "
            f"echo QUEUE_CORRUPT=$(grep -c QUEUE_CORRUPT {LOG}); "
            f"echo boots=$(grep -c 'SIPP build' {LOG})"
        )
        print(con.cmd(script, 30))
        return 0
    finally:
        print("=== restoring power ===", flush=True)
        power("on")


if __name__ == "__main__":
    sys.exit(main())
