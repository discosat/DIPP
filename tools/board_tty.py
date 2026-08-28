#!/usr/bin/env python3
"""
Find the board's console port instead of hardcoding one.

The bench has two identical-looking Prolific adapters and the assignment is not
stable: after the server was physically moved and rebooted, the Otii shifted
from behind a hub to a direct port, both adapters changed USB ports, and the
console moved from /dev/ttyUSB0 to /dev/ttyUSB1. Every tool that hardcoded
ttyUSB0 would have talked to a dead port and reported the board as unresponsive.

Probes each candidate for something that answers like a Linux console. Override
with BOARD_TTY=/dev/ttyUSBn to skip detection.

NEVER probes /dev/ttyACM0 -- that is the Otii Ace.
"""
import os, glob, time, termios, select

CACHE = None


def _open(dev):
    fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[0] = a[1] = a[3] = 0
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    a[4] = a[5] = termios.B115200
    a[6] = list(a[6]); a[6][termios.VMIN] = 0; a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)
    return fd


def _drain(fd, secs):
    out = b""; end = time.time() + secs
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try: out += os.read(fd, 8192)
            except BlockingIOError: pass
    return out.decode("utf-8", "replace")


def _looks_like_console(dev):
    """A bare CR should draw a login prompt or a shell prompt.

    Safe to poke here: this runs against a board that has been up for a while,
    not one mid-boot. Poking during U-Boot's countdown would stop autoboot.
    """
    try:
        fd = _open(dev)
    except Exception:
        return False
    try:
        _drain(fd, 0.5)
        # Two probes: the console may be sitting at a Password: prompt left by a
        # stray keystroke, where the first CR only produces "Login incorrect"
        # and the real prompt follows a beat later.
        out = ""
        for _ in range(2):
            os.write(fd, b"\r")
            out += _drain(fd, 3.0)
            if any(k in out for k in ("login:", "Password:", "root@", "PicoCore")):
                return True
            if out.rstrip().endswith("#"):
                return True
        return False
    finally:
        os.close(fd)


def resolve(verbose=False):
    global CACHE
    if CACHE:
        return CACHE
    env = os.environ.get("BOARD_TTY")
    if env:
        CACHE = env
        return CACHE
    for dev in sorted(glob.glob("/dev/ttyUSB*")):
        if _looks_like_console(dev):
            if verbose:
                print(f"[board_tty] console detected on {dev}")
            CACHE = dev
            return CACHE
    raise RuntimeError(
        "no board console found on any /dev/ttyUSB*. Check the console cable, "
        "and that the board is powered (tools/otii_power.py status)."
    )


if __name__ == "__main__":
    print(resolve(verbose=True))
