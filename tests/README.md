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

**Correction (2026-08-28).** An earlier version of this note claimed the board's
page size exceeds 4096 and that this is why a 4096-granularity build failed with
`EINVAL` on hardware. That is wrong: the board measures **4 kB**
(`/proc/self/smaps` `KernelPageSize`). The run that produced 64 `QUEUE_SYNC_FAIL`
was launched before the header was widened and was still running the
**512-byte-header** build, whose slot[0] at offset 512 is unaligned on any page
size. The failure was real; the attribution was not.

The layout keeps 64 KiB granularity anyway — headroom against a kernel built
with 16K or 64K pages — and `init` logs `QUEUE_ALIGN_WARN` if the runtime page
size ever fails to divide it.

**Always confirm on the board** after changing anything about the on-disk
layout: check `QUEUE_SYNC_FAIL` is 0 in the daemon log. A host-side pass proves
the logic, not the alignment.
