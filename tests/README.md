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
