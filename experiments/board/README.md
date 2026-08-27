# Artefacts recovered from the flight-model board

Pulled from the live board over the serial console on 2026-08-27 and verified
by md5 at both ends. **These existed nowhere else** — the copies on the USB
flash and in the host archive are stale.

| File | Bytes | md5 | Source on board |
|---|---|---|---|
| `poisson_process_experiment` | 1034 | `eaddafd55af9bd8556b32b5d7769931e` | `/home/root/` |
| `populate_queue` | 681 | `ef9702159dc5b722ec0e2499feb58e5a` | `/home/root/` |
| `bash_history.txt` | 20119 | `b31d8fabc3465b36acc1526e9d3111a0` | `/home/root/.bash_history` |
| `cost.cache` | 3200 | `ac764424176cac1062f7ae8aff734d80` | `/usr/share/dipp/cost.cache` |

## Why the archived copies are wrong

The history contains `cp /mnt/flash/2025-11-18/poisson_process_experiment .`
— the scripts were copied off the flash and then **edited in place on the
board**. The flash and host-archive copies are the pre-edit versions:

| | Flash / host archive | **Board (what actually ran)** |
|---|---|---|
| `EVENTS` | 300 | **1000** |
| `MEAN_INTERVAL` | 2 | **3** |
| seed | `srand()` — unseeded | **`srand(43)`** |
| settle delay | none | **`sleep 10`** |
| pipeline | 1 | **3** |
| `populate_queue` args to `emulate_camera` | 3 (no `num_images`) | **4** |
| size | 1021 / 679 | 1034 / 681 |

So the arrival stream **was** seeded and identical across runs, and
`emulate_camera 1 3 123` means `num_images=1` — **"3i" in trace names is the
3-second interval, not 3 images.**

## How runs were launched

```
./dipp -i CAN -p can0 -a 5423 -t traces/<name>.json
```

Trace names give the experiment matrix: `decomposed-{mem,mmap}-{high,low}`,
`single-mem-{high,low}`, `poisson-{mem,mmap}[-long]`, `crash-{mem,mmap}`,
`dynamic-mem-low`. Reuse these names in the Stage 4 harness.

`populate_queue` was invoked with pipelines 1, 4 and 5 (`10 1 …`, `10 4 …`,
`1 5 …`); the Poisson script separately drives pipeline 3.

## Cross-checks performed on the live board

- **Experiment binary confirmed.** `/mnt/flash/2025-11-18/dipp` is
  `139a974989235f2a8c577e1e9f1e6ed2` — identical to the host archive, and the
  binary `PROVENANCE.md` verifies the reconstruction against. `dipp-old` and
  `emulate_camera` match the archive too. The host archive is faithful.
- **`/usr/bin/dipp` is not the experiment build.** It symlinks `dipp-10-31`
  (`4bcef27e…`, matching `~/fresh-flash/2025-10-31-roba/`). The experiment
  binary lives only on the USB flash.
- **`module_timeout = 0`** — read from `/storage.vmem` at `VMEM_MODULE_TIMEOUT`
  (`0x131D`): `00 00 00 00`. Answers open question U1: `alarm(0)` **cancels**
  rather than schedules, `MODULE_EXIT_TIMEOUT` is unreachable, and the
  bounded-execution claim is untested. Must be set (15–20 s) before the A8
  fault runs.
- **412 orphaned files** remain in `/usr/share/dipp/data/`, with both
  `queue_file` and `partially_processed_queue_file` at exactly 20,056 bytes —
  the resource leak (A8) and the queue-write granularity (A7) both confirmed
  on hardware.
- Module `.so` sizes in `/usr/share/pipeline/` match the manuscript's Table V
  inputs exactly.

## Notes for whoever repeats this

- Console is **`/dev/ttyUSB0`** at 115200 (not `ttyUSB1`); log in as `root`
  with an empty password. Send a bare `\r` — sending `\r\n` double-submits on
  a raw tty and the login fails.
- The board is BusyBox: no `base64`, `head -4` is invalid (use `head -n 4`).
  Transfer binaries with `od -v -An -tx1`; **`-v` is required** or repeated
  lines collapse to `*` and the file is silently truncated.
- The board RTC is unset (reads 2022), so on-board mtimes are meaningless —
  rely on md5.
- `/mnt/flash` is `/dev/sda1` and is not mounted at boot:
  `mount -o ro /dev/sda1 /mnt/flash`.
