#!/usr/bin/env python3
"""
Push a file to the board over the serial console, 8-bit clean.

    board_push.py <local> <remote> [--chunk 512] [--pace 0.02]

Why this shape:
  - The board is BusyBox: no base64, no uudecode, no xxd. Hex-via-awk cannot
    work either, because awk strings cannot hold NUL bytes and compressed data
    is full of them. So the bytes go across raw.
  - The whole remote command is submitted as ONE line while the tty is still in
    cooked mode. The shell buffers it, and only then does `stty raw` take
    effect -- otherwise CR would stop being a line terminator mid-command and
    the shell would never run anything.
  - The receiver is `dd bs=1 count=N`, which reads exactly N bytes and exits,
    so no EOF has to be signalled (impossible in raw mode). NOT `head -c N`:
    BusyBox head reads ZERO bytes from a tty -- verified, it produced an empty
    file while dd on the same payload produced all of it. bs=1 costs one
    syscall per byte, which at 11.5 KB/s is nothing.
  - There is no hardware flow control, so writes are paced to the LINE RATE.
    115200 8N1 carries 11,520 B/s; pushing faster just overruns the board's tty
    buffer and silently loses bytes. Pacing is derived from the baud rate with
    a margin rather than guessed -- a 4 KB test passes even when overdriven,
    because the buffer absorbs it, so a small test does not prove the rate.

Always verifies with md5 at both ends.
"""
import os, sys, time, termios, select, hashlib, argparse

DEV = "/dev/ttyUSB0"

def open_port():
    fd = os.open(DEV, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[0] = a[1] = a[3] = 0
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    a[4] = a[5] = termios.B115200
    a[6] = list(a[6]); a[6][termios.VMIN] = 0; a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)
    return fd

def drain(fd, secs):
    out = b""; end = time.time() + secs
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try: out += os.read(fd, 8192)
            except BlockingIOError: pass
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("local"); ap.add_argument("remote")
    ap.add_argument("--chunk", type=int, default=512)
    ap.add_argument("--pace", type=float, default=None,
                    help="seconds per chunk; default derives from 115200 baud with 15%% margin")
    a = ap.parse_args()

    BYTES_PER_SEC = 115200 / 10.0          # 8N1: 10 bits on the wire per byte
    if a.pace is None:
        a.pace = (a.chunk / BYTES_PER_SEC) * 1.15
    data = open(a.local, "rb").read()
    want = hashlib.md5(data).hexdigest()
    n = len(data)

    fd = open_port()
    drain(fd, 0.5)
    os.write(fd, b"\r"); drain(fd, 1.0)

    cmd = (f"stty raw -echo -ixon -ixoff; "
           f"dd of={a.remote} bs=1 count={n} 2>/dev/null; stty sane\r")
    os.write(fd, cmd.encode())
    time.sleep(1.2)                      # let the shell reach head before streaming
    drain(fd, 0.3)

    t0 = time.time(); sent = 0
    for i in range(0, n, a.chunk):
        os.write(fd, data[i:i + a.chunk])
        sent += len(data[i:i + a.chunk])
        time.sleep(a.pace)
        if sent % (a.chunk * 40) == 0 or sent == n:
            el = time.time() - t0
            print(f"\r  {sent}/{n} B  {sent/max(el,0.001)/1024:.1f} KB/s", end="", flush=True)
    print()
    time.sleep(1.5)
    drain(fd, 2.0)

    os.write(fd, f"sync; md5sum {a.remote}\r".encode())
    out = drain(fd, 20).decode("utf-8", "replace")
    got = None
    for line in out.splitlines():
        parts = line.split()
        if parts and len(parts[0]) == 32 and all(c in "0123456789abcdef" for c in parts[0]):
            got = parts[0]
    print(f"  local  md5 {want}")
    print(f"  remote md5 {got}")
    if got == want:
        print("  MATCH"); return 0
    print("  MISMATCH"); print(out[-400:]); return 1

if __name__ == "__main__":
    sys.exit(main())
