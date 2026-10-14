#!/usr/bin/env python3
"""Copy flight models from the board to the host over the serial console.

The board has no network link, and board_fetch.py moves one file as a hex
dump with a 240 s limit (~11 KB/s), so a file of several MB is sent in
pieces: gzip -9 on the board, cut into PIECE bytes with dd, each piece
fetched and md5-verified by board_fetch.py (retried), then joined,
decompressed and checked against the md5 of the original file on the board.

    python3 tools/fetch_board_models.py OUTDIR NAME:MD5 [NAME:MD5 ...]

NAME is a file in /usr/share/models/ on the board. Never run while a bench
run holds the console.
"""
import gzip, hashlib, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REMOTE_DIR = "/usr/share/models"
PIECE = 60000
TRIES = 3


def board(cmd, timeout=300):
    r = subprocess.run([os.path.join(HERE, "board_console.py"), cmd], capture_output=True, text=True,
                       timeout=timeout)
    return r.stdout


def remote_md5(path):
    for line in board(f"md5sum {path} 2>/dev/null").splitlines():
        p = line.split()
        if len(p) == 2 and len(p[0]) == 32 and p[1].endswith(os.path.basename(path)):
            return p[0]
    return None


def fetch(remote, local, md5):
    r = subprocess.run([os.path.join(HERE, "board_fetch.py"), remote, local, md5], capture_output=True,
                       text=True, timeout=600)
    return r.returncode == 0 and "-> MATCH" in r.stdout


def one(outdir, name, want):
    src = f"{REMOTE_DIR}/{name}"
    have = remote_md5(src)
    if have != want:
        raise SystemExit(f"{name}: md5 on the board is {have}, expected {want}")
    gz = f"/tmp/fbm_{name}.gz"
    out = board(f"gzip -9 -c {src} > {gz}; echo SZ=$(wc -c < {gz})")
    m = re.findall(r"^SZ=(\d+)\s*$", out, re.M)
    if not m:
        raise SystemExit(f"{name}: no size from the board:\n{out}")
    size = int(m[-1])
    n = (size + PIECE - 1) // PIECE
    print(f"{name}: {size} B compressed, {n} pieces", flush=True)
    blob = b""
    t0 = time.time()
    for i in range(n):
        part = f"/tmp/fbm_part"
        board(f"dd if={gz} of={part} bs={PIECE} skip={i} count=1 2>/dev/null")
        md5 = remote_md5(part)
        if not md5:
            raise SystemExit(f"{name}: piece {i}: no md5 from the board")
        local = os.path.join(outdir, f".fbm_{name}.{i}")
        for k in range(TRIES):
            if fetch(part, local, md5):
                break
            print(f"  piece {i}: retry {k + 1}", flush=True)
        else:
            raise SystemExit(f"{name}: piece {i} failed {TRIES} times")
        blob += open(local, "rb").read()
        os.remove(local)
        print(f"  {i + 1}/{n}  {time.time() - t0:.0f} s", flush=True)
    data = gzip.decompress(blob)
    got = hashlib.md5(data).hexdigest()
    if got != want:
        raise SystemExit(f"{name}: joined file md5 {got} != board {want}")
    with open(os.path.join(outdir, name), "wb") as fh:
        fh.write(data)
    board(f"rm -f {gz} /tmp/fbm_part")
    print(f"{name}: OK ({len(data)} B, md5 {got})", flush=True)


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    for spec in sys.argv[2:]:
        name, md5 = spec.split(":")
        one(outdir, name, md5)
    print("ALLDONE", flush=True)


if __name__ == "__main__":
    main()
