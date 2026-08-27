# Bench tooling

Written during Stage 0. All three exist because the obvious approach fails in
a way that is easy to misdiagnose.

| Script | What it does |
|---|---|
| `build_id.sh` | Emits `git describe --dirty` + submodule SHAs for `-DBUILD_ID`. Degrades to `nogit`, never fails a build. |
| `board_console.py` | Runs commands on the board console and returns the output. |
| `board_fetch.py` | Pulls a file off the board as a hex dump and verifies it by md5. |
| `csh_drive.py` | Drives `csh` through a pty and strips the ANSI redraw noise. |
| `board_push.py` | Pushes a file TO the board over the serial console, 8-bit clean, md5-verified. |

## Usage

```sh
tools/board_console.py "ls -la /usr/share/dipp/"
tools/board_fetch.py /usr/share/dipp/cost.cache ./cost.cache <expected-md5>
tools/csh_drive.py /home/roba/csh-new/csh "./csh -i can.csh" "ping 5423"
tools/board_push.py ./payload.tar.gz /tmp/payload.tar.gz
```

## Deploying without physical access

Ethernet is dead and the USB stick needs hands, but the serial console is
enough if the payload is shrunk first:

```sh
# strip -- runtime-neutral, proven in analysis/table5_module_sizes.py
aarch64-linux-gnu-strip --strip-all *.so dipp emulate_camera
tar czf payload.tar.gz .          # 9.86 MB -> 232 KB, a 42x reduction
tools/board_push.py payload.tar.gz /tmp/payload.tar.gz
```

232 KB takes about 24 s at 9.7 KB/s. Unpack with `tar xzf` on the board.

**A3 changed `ImageBatch`, so DIPP and the modules must be deployed together** --
mixing them corrupts every field after `priority`.

## Why each one is not a one-liner

**`board_console.py`** — the console is `/dev/ttyUSB0` at 115200 (not
`ttyUSB1`), login `root` with an empty password. Send a bare `\r`: `\r\n`
double-submits on a raw tty, so the blank second line is consumed as the
password and the login fails with "Login incorrect". That failure looks
exactly like a wrong password.

**`board_fetch.py`** — the board is BusyBox, so there is no `base64`. Transfer
is `od -v -An -tx1`, and **`-v` is mandatory**: without it `od` collapses
repeated identical lines to `*` and the file silently truncates. A 3200-byte
`cost.cache` came back as 464 bytes and only the md5 check caught it. The
drain also has to wait for the end marker to appear *twice*, because the
console echoes the command containing it before any output arrives.

**`board_push.py`** — three things make this harder than `cat > file`:

- BusyBox has no `base64`, no `uudecode`, no `xxd`, so the bytes must cross
  raw. Hex-via-`awk` is not an option either: awk strings cannot hold NUL, and
  compressed data is full of them.
- **`head -c N` reads ZERO bytes from a tty** — verified, it produced an empty
  file while `dd bs=1 count=N` on the same payload produced all of it. Use `dd`.
- The whole remote command goes across as ONE line while the tty is still
  cooked. The shell buffers it, and only then does `stty raw` take effect;
  otherwise CR stops terminating lines mid-command and nothing runs.
- There is no hardware flow control, so writes are paced to the **line rate**
  (115200 8N1 = 11,520 B/s). A 4 KB test passes even when overdriven because
  the tty buffer absorbs it, so a small test does not prove the rate.

**`csh_drive.py`** — `csh` aborts with "Failed to init slash" unless it has a
TTY, hence `pty.fork()`. Its line editor redraws the prompt per keystroke, so
raw output is unreadable without stripping ANSI. Note the syntax is
`csp add can -d <addr>` — passing a device name gives "Addr argument 'can0' is
not an integer" — and the host must sit in the board's subnet (netmask 8 ->
5376..5439), so use `csh-new/csh/can.csh` at node 5411, not the
`dipp2-experiments` copy at node 15.

## Other board gotchas

- BusyBox `find` has no `-delete`; use `-exec rm -f {} +`.
- `head -4` is invalid; use `head -n 4`.
- `ipcs` does not exist; read `/proc/sysvipc/shm`.
- The RTC is unset (reads 2022), so on-board mtimes are meaningless.
- `/mnt/flash` is `/dev/sda1` and is not mounted at boot.
