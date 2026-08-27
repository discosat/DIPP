# The flight configuration, recovered and versioned

Decoded from `/home/root/tmp-experiments/storage.vmem` on the board
(md5 `01fbcd00ef428ef1fb371f2ae795199e`). `tools/make_storage_vmem.py build
manifest.yaml` reproduces that file with **all 20 slots byte-identical** in the
meaningful region (length byte + Brotli stream); only the ignored tail padding
differs, because the original carries stale bytes from earlier longer writes
while we zero it.

This is the configuration the paper's results were produced with. It was never
under version control before.

**The experiments ran from `/home/root/tmp-experiments/`, not `/home/root`.**
`VMEM_DEFINE_FILE(storage, "storage", "storage.vmem", 10000)` takes a
**relative** path, so the daemon uses whichever `storage.vmem` is in its cwd,
and the board history shows repeated `cd tmp-experiments/`. Two other copies
exist and are *not* the experiment config: `/storage.vmem` is in the older
pre-multi-effort schema (`order`/`name`/`param_id`, commit `026bd79`), and
`/home/root/storage.vmem` was created empty by a daemon run.

## Pipeline map — refined

| # | Role | Modules (param:effort) |
|---|---|---|
| 1 | **adaptive**, multi-effort | demosaic 1 · tile 2 · classification 3/4/5 (low/med/high) · segmentation 6/7 (low/high) · jxl 11/12/13 (low/med/high) |
| 2 | **static LOW** | demosaic 1 · tile 2 · classification **3** · segmentation **6** · jxl **11** |
| 3 | **static HIGH** | demosaic 1 · tile 2 · classification **5** · segmentation **7** · jxl **13** |
| 4 | monolith | `libsingle_module` 9/10 (low/high) |
| 5 | demosaic + jxl | demosaic 1 · jxl 13 |

The plan previously recorded 2 and 3 only as "static, default-only". They are
**not interchangeable**: 2 pins every stage to its low variant, 3 pins every
stage to its high variant.

**Consequence:** `poisson_process_experiment` runs `emulate_camera 1 3 123`, so
**the Poisson runs used pipeline 3 — static HIGH, not adaptive and not low.**
This is exactly the correction the author gave during the audit ("I updated the
yaml configs for those runs to contain the default efforts only which were the
high effort entries from the adaptive one"), now confirmed from the bytes.

## Module costs as configured

| param | latency µs | energy µWh | what |
|---:|---:|---:|---|
| 1 | 373,000 | 718 | demosaic |
| 2 | 143,000 | 264 | tile |
| 3 / 4 / 5 | 235,000 / 598,000 / 1,560,000 | 465 / 1,200 / 3,180 | classification low/med/high |
| 6 / 7 | 120,000 / 1,472,000 | 220 / 3,540 | segmentation low/high |
| 8 | 100 | 59 | save_image |
| **9 / 10 / 14** | **10** | **5** | **monolith — placeholder hints** |
| 11 / 12 / 13 | 149,000 / 1,689,000 / 5,845,000 | 264 / 3,010 / **10,300** | jxl EL1/EL4/EL7 |
| 15 | 10 | 5 | crash_and_burn, `fail_case=1` |

Four things this settles:

- **The monolith's cost hints are placeholders** — 10 µs and 5 µWh against real
  values in the hundreds of thousands. Every scheduling decision involving
  pipeline 4 was driven by figures three to five orders of magnitude too small.
  This is new, and it matters for the Fig. 5 monolithic arm and for C7e.
- **A9 confirmed:** `threshold_percentage` is 0.05 at low effort and 0.7 at
  high, so low retains strictly more tiles by construction; and `class_index`
  is **7** (Residential), not 9 (SeaLake).
- **C10.1 confirmed:** JPEG-XL at EL7 costs 10,300 µWh — more than the largest
  inference module (segmentation high, 3,540 µWh). The encoder really is the
  dominant energy consumer at high effort.
- **`module_timeout = 0` in the experiment configuration itself**, not merely
  on the board today. So `MODULE_EXIT_TIMEOUT` was unreachable *during the
  reported fault experiments*.

`error_code` at `0x1318` reads 500000 = code 500 (`INTERNAL_PID_NOT_FOUND`).
It is runtime output rather than configuration, so the manifest does not carry
it and a rebuild leaves it zero.
