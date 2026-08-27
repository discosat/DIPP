# Provenance of the SIPP IoT-J experiment build

Written during resubmission Stage 0 (2026-08-27). Everything here was
verified against artefacts, not inferred from commit messages.

## Summary

The paper's results were produced by a source tree that **no longer exists**.
It was not any commit as-committed. This branch (`resubmission`) is a
reconstruction of it, and every element of the reconstruction is confirmed
against the deployed binary.

| | |
|---|---|
| Deployed daemon | `/home/roba/dipp2-experiments/2025-11-18/dipp` |
| md5 | `139a974989235f2a8c577e1e9f1e6ed2` |
| Built | 2025-12-02 |
| Original build tree | `/home/roba/DIPP/builddir` (`DW_AT_comp_dir`) — **deleted** |
| Reconstruction | tag `paper-experiments` (577c225) + commit `ca04831` |

## What the binary proves

**Base commit is 577c225, not the branch tip.**
`get_message_from_queue` takes two arguments (`x0`=datarcv, `w1`=do_wait) and
calls `image_batch_setup_storage` unconditionally at `6f20`. Both call sites
set up only two registers. Commit `f21f22a` (2025-12-30, message "Updated
readme") adds a third argument plus an early `return FAILURE` that makes the
storage call unreachable — that refactor is **absent** from the deployed
binary, so image persistence was working during the experiments.

**Three constants were uncommitted working-tree edits.**

| Constant | 577c225 | Deployed | Later committed in f21f22a |
|---|---|---|---|
| `LOW_QUEUE_DEPTH_THRESHOLD` | 30 | **5** | 5 |
| `BEST_EFFORT_MAX_LATENCY_MEDIUM_EFFORT` | 3000000 | **5000000** | 5000000 |
| `BEST_EFFORT_MAX_LATENCY_LOW_EFFORT` | 1000000 | **3000000** | 3000000 |

Evidence: `update_heuristic` compiles to `cmp w19, #0x4` (`total < 5`);
`get_best_effort_implementation_config` compares against `#0x4C4B3F`
(4999999) and `#0x2DC6BF` (2999999).

The developer evidently had these edits in the working tree on 2025-12-02,
built and deployed, then on 2025-12-30 committed them together with the
unrelated (and defective) queue-depth refactor.

**Manuscript consequence:** the best-effort latency gates in the reported runs
were **5 s and 3 s**, not 3 s and 1 s. W2 must document these values.

**Submodule pins are the ones recorded by 577c225.**
Determined by comparing maximum DWARF line numbers per compiled source
against each candidate commit:

| Source | DWARF max | 6d0c670a | **8cc13c6** |
|---|---|---|---|
| `csp_buffer.c` | 204 | 167 | **204** |
| `csp_service_handler.c` | 336 | 284 | **336** |
| `can_socketcan.c` | 300 | 260 | **300** |
| `csp_rdp.c` | 969 | 955 | **969** |

| Source | DWARF max | 8215d37d | **768970c6** |
|---|---|---|---|
| `param_list.c` | 816 | 734 | **815** |
| `vmem.c` | 134 | 75 | **135** |
| `vmem_ring.c` | 209 | 142 | **209** |
| `vmem_server.c` | 467 | 424 | **472** |

So `lib/csp=8cc13c6`, `lib/param=768970c6` — see `SUBMODULES.lock`.

**This retires an earlier audit finding.** The audit reported the submodules
as "246 commits behind pin ... a reproducibility landmine". That described
the stale clone at `/home/roba/dipp-paper/DIPP` (reflog ends 2025-10-23 at
2d5a8b7; it has never contained 577c225). That tree was never the build tree.
The actual build used the recorded pins exactly. **False alarm.**

## Modules

| | |
|---|---|
| Deployed | `/home/roba/dipp2-experiments/2025-11-18/modules/*.so`, built 2025-12-05 |
| Original build tree | `/home/roba/dipp-image-classification-pipeline/builddir` — **deleted** |
| Baseline | `dipp-image-classification-pipeline` tag `paper-experiments` (fb6ffd4) |

No compiled source exceeds its length at `fb6ffd4`, so the modules are
*consistent* with that commit. This is a consistency check, not a proof of
byte-identity — line counts cannot detect a same-length edit, and the DIPP
constants above show such edits did happen elsewhere.

`DW_AT_producer` for `libcrash_and_burn.so` records
`-O2 -O2 -O3 -fstack-protector-strong`, confirming plan item **A8**: the fault
module was optimised, so the UB-based fault cases are deleted.

Raw `.so` sizes match the manuscript's Table V inputs exactly.

## Preserved local state

Nothing was discarded. Branches created in Stage 0:

| Repo | Branch/ref | Holds |
|---|---|---|
| DIPP | `local/state-2025-10-23` | working tree as found (submodule pointer + empty `environment_tracking` stub) |
| DIPP | `local/stash-main-demosaic` | the pre-existing stash, made reachable |
| DIPP `lib/param` | `local/vmem-name-len` | uncommitted `strncpy` 5→4 edit |

The `strncpy` edit is **dead code**: `vmem_client.c` is not among the libparam
sources compiled into the daemon, and the only DIPP call site
(`src/vmem/vmem_upload_local.c`) is commented out at both 2d5a8b7 and 577c225.
It cannot explain any measured behaviour. A plain-file copy of the patch is
also at `/home/roba/dipp-paper/stage0-provenance/`.

## Rebuild verification

Rebuilt from this branch with the SDK at
`/opt/fus-imx-disco/5.15-kirkstone`; every signature matches the deployed
binary:

| Check | Rebuild | Deployed |
|---|---|---|
| `update_heuristic` threshold | `cmp w19, #0x4` | `cmp w19, #0x4` |
| medium-effort gate | `movk w1, #0x4c` | `movk w1, #0x4c` |
| low-effort gate | `movk w1, #0x2d` | `movk w1, #0x2d` |
| `get_message_from_queue` 3rd arg | absent | absent |
| `image_batch_setup_storage` call sites | 2 | 2 |

## Still outstanding

The board's real `poisson_process_experiment` and `populate_queue` are **not
yet committed**. The copies in `/home/roba/dipp2-experiments/2025-11-18/` are
stale (300 events, `MEAN_INTERVAL=2`, unseeded `srand()`, 3-argument
`populate_queue`); the versions actually used (1000 events, `MEAN_INTERVAL=3`,
`srand(43)`, pipeline 3, 4-argument `populate_queue`) exist only on the
device. The board was powered off during Stage 0 and must be brought up to
recover them.
