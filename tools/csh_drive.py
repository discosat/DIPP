#!/usr/bin/env python3
"""Drive csh through a pty (it refuses to init slash without a TTY)."""
import os, sys, pty, time, select, termios, struct, fcntl

cwd  = sys.argv[1]
argv = sys.argv[2].split()
cmds = sys.argv[3:]

pid, fd = pty.fork()
if pid == 0:
    os.chdir(cwd)
    os.execv(argv[0], argv)

fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
out = b""
def pump(sec):
    global out
    end = time.time() + sec
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                d = os.read(fd, 4096)
                if not d: return
                out += d
            except OSError: return

pump(4)
for c in cmds:
    os.write(fd, (c + "\n").encode())
    pump(6)
os.write(fd, b"exit\n"); pump(1)
try: os.kill(pid, 9)
except OSError: pass
import re
txt = out.decode("utf-8", "replace")
txt = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", txt)      # strip ANSI
txt = re.sub(r"\x1b[\(\)][A-Z0-9]", "", txt)
lines = [l.rstrip() for l in txt.split("\n")]
seen_prompt = re.compile(r"^\s*robas-desktop\s*$")
keep = [l for l in lines if l.strip() and not seen_prompt.match(l)]
print("\n".join(keep))
