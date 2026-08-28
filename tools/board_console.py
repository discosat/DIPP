#!/usr/bin/env python3
"""Minimal expect-style driver for the board console on /dev/ttyUSB0."""
import os, sys, time, termios, select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_tty
DEV = board_tty.resolve()
def open_port():
    fd = os.open(DEV, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[0] = a[1] = a[3] = 0                      # iflag oflag lflag: raw
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    a[4] = a[5] = termios.B115200
    a[6] = list(a[6]); a[6][termios.VMIN] = 0; a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)
    return fd

def drain(fd, secs):
    out = b""; end = time.time() + secs
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try: out += os.read(fd, 4096)
            except BlockingIOError: pass
    return out

def send(fd, s):
    os.write(fd, (s + "\r").encode())           # single CR only

def ensure_logged_in(fd):
    """Get to a root shell, whatever state the console was left in.

    The board presents a login prompt after every reboot, and a stray keystroke
    can leave it at Password:. Always send a BARE CR -- "\\r\\n" double-submits on
    a raw tty, so the empty second line is eaten as the password and the login
    fails in a way that looks exactly like a wrong password.
    """
    for _ in range(4):
        os.write(fd, b"\r")
        out = drain(fd, 2.5).decode("utf-8", "replace")
        if "root@" in out or out.rstrip().endswith("#"):
            return True
        if "Password:" in out:
            os.write(fd, b"\r")     # abandon it; the login prompt comes back
            drain(fd, 2.5)
            continue
        if "login:" in out:
            os.write(fd, b"root\r")
            out2 = drain(fd, 5).decode("utf-8", "replace")
            if "root@" in out2 or out2.rstrip().endswith("#"):
                return True
    return False


if __name__ == "__main__":
    fd = open_port()
    drain(fd, 0.5)
    ensure_logged_in(fd)
    send(fd, "")
    print(drain(fd, 2).decode("utf-8", "replace"), end="")
    for arg in sys.argv[1:]:
        send(fd, arg)
        print(drain(fd, 3).decode("utf-8", "replace"), end="")
