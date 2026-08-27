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

## Bench verification, 2026-08-27

Ran with CAN up at 1 Mbit/s, sample-point 0.750, ERROR-ACTIVE, zero error
counters on every register.

**The CSP path works end to end.** This matters because the historical
telemetry failure was never diagnosed:

```
ping 5423                  -> Reply in 2 [ms]
list download 5423         -> Received 31 parameters, of which 0 were skipped
```

Registered params include `module_param_1..12` at 188 bytes each (matching
`DATA_PARAM_SIZE`), plus `log_status`, `serial0`, `radio_node_id`,
`module_timeout` and `battery_level`. So the transport and the param server
are both healthy — consistent with the diagnosis that the failure was
unregistered params / `promisc = 0` / blocking pulls, not the wire.

**`module_timeout = 0` confirmed twice, independently:** the raw
`/storage.vmem` byte at `0x131D`, and `get -n 5423 module_timeout` over CSP.
Live values read at the same time:

| Param | Value |
|---|---|
| `module_timeout` | **0** |
| `battery_level` | 74.128899 |
| `radio_node_id` | 0 |
| `log_status` | 200000 |

`log_status = 200000` decodes through `code*1000 + pipeline*100 + module` to
code **200 = `MSGQ_NOT_FOUND`** — expected with no producer running.

**The A8 leak, quantified.** Clearing one run's leftovers took the filesystem
from 3.2 G used (59%) to 621 M (11%): **412 orphaned files were holding
2.6 GB, 46% of the 5.7 GB partition.** At that rate the device fills after
roughly two runs. Good numbers for C9/C12.

**The idle spin loop is hot.** With no producer, the daemon logged
`Error: 200000` at roughly 600–900 lines/second — 148,371 lines in a single
short session. Confirms A5's "stop tracing the idle spin loop".

**`SIGINT` is not enough.** `kill -INT` did **not** stop the daemon when
tracing was disabled (the handler is only registered under `-t`); `kill -TERM`
did. The harness must send SIGTERM for untraced runs, and A5's "handle
SIGTERM, not just SIGINT" is confirmed as necessary.

### Host-side notes

- `csh` needs a TTY — drive it through a pty or it fails with "Failed to init
  slash". A working driver is in the scratchpad.
- Syntax is `csp add can -d <addr>`; passing a device name is rejected with
  "Addr argument 'can0' is not an integer".
- Use **`csh-new/csh/can.csh`** (host node **5411**). The copy in
  `dipp2-experiments/csh/init/can.csh` uses node 15, which is outside the
  board's subnet (netmask 8 -> 5376..5439) and will not route to 5423.
- csh tries to reconfigure `can0` and prints "RTNETLINK answers: Operation not
  permitted" unless run as root. Harmless if the interface is already up with
  the right parameters.
- `ipcs` does not exist on the board; read `/proc/sysvipc/shm` instead. Update
  the Stage 4 harness accordingly.

## Persisted queue sample

`queue_file` and `partially_processed_queue_file` are the mmap-backed priority
queues as left by the final run, both exactly **20,056 bytes** — the whole
structure that `msync` writes as a unit, which is what A7's torn-write concern
is about.

| File | md5 | Non-zero bytes |
|---|---|---|
| `queue_file` | `5f17134ba3787d94c55d40d5c90953e7` | 16,189 / 20,056 |
| `partially_processed_queue_file` | `64ebf94c99b88f15dc1970104dcd610b` | 1,622 / 20,056 |

The ingest queue was **not empty** when the run ended. Kept as a real sample
of the on-disk format for writing the Stage 3 recovery path and orphan scan,
and for testing the decoder offline without occupying the board.
