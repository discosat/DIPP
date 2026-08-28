# Tests

## `test_trace_fork.c`

Regression test for the defect that made every archived trace invalid JSON.

```sh
gcc -o /tmp/test_trace_fork tests/test_trace_fork.c src/utils/minitrace.c \
    -Isrc/include -Isrc/include/utils -DMTR_ENABLED=1 -pthread
/tmp/test_trace_fork
```

Builds natively; minitrace is portable, so this needs no board and no SDK.

**Verified to catch the defect.** Rebuilt with the two flushes removed (the
`fflush(f)` after the header in `mtr_init_from_stream`, and the `fflush(NULL)`
before `fork()`), the same test reports:

```
FAIL: 201 headers, expected exactly 1
```

201 headers for 200 forked children — the same N+1 pattern the audit found in
the archived traces (~501 headers for ~500 module executions). With the fixes
in place it reports exactly 1 header and 0 dropped events.

Note the corrupt trace is *larger* than the correct one (3,607 B vs 209 B):
the duplicated headers were most of the file.

## `test_shadow_paging.c`

```sh
gcc -o /tmp/test_shadow tests/test_shadow_paging.c \
    src/priority_queue/priority_queue_mmap.c src/priority_queue/priority_queue.c \
    src/priority_queue/priority_queue_mem.c \
    -Isrc/include -Isrc/include/priority_queue -Isrc/include/image \
    -Isrc/include/utils -Isrc/include/protobuf -Isrc/include/cost_store -pthread
/tmp/test_shadow
```

Writes the states a power cut can leave behind directly into the queue file,
which is deterministic where a real cut is a race. Six cases: clean recovery,
torn inactive slot (ignored), **torn active slot (must roll back to the other
slot)**, torn header (detected, not trusted), and that the file stays usable
afterwards.

It immediately caught a defect that would have made the whole mechanism
useless: **`msync()` requires a page-aligned address.** With the header at 512 B
and slots at 20,808 B, every slot `msync` failed with `EINVAL` and nothing ever
reached storage — silently.

**But this test cannot catch that class of bug on its own, and did not.** The
first fix used 4096-byte granularity, which is the host's page size, so the test
passed on x86 while the board still failed with `EINVAL` on every single
`msync` — 64 of them in one run, with the queue never persisted at all. The
layout now uses 64 KiB granularity, a multiple of every page size Linux uses on
aarch64 (4K/16K/64K), and `init` logs `QUEUE_ALIGN_WARN` if the runtime page
size ever fails to divide it.

**Always confirm on the board** after changing anything about the on-disk
layout: check `QUEUE_SYNC_FAIL` is 0 in the daemon log. A host-side pass proves
the logic, not the alignment.
