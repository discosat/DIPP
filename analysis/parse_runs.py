#!/usr/bin/env python3
"""
Turn raw run artifacts into two tidy CSVs. This is the thing that was never
written: both previous notebook generations regex-scraped the daemon's stdout,
and nothing ever read the trace JSON at all.

    parse_runs.py RUNDIR [RUNDIR ...] -o outdir
    parse_runs.py --console log.txt [--trace trace.json] -o outdir

Emits:
    executions.csv  one row per module execution
    runs.csv        one row per run

The EXEC line is the primary source, not the trace. It already carries every
field the tables need, it is line-buffered so it survives a kill, and
MTR_MAX_ARGS is 1 so the trace could not carry them anyway. The trace is the
timeline source (Figs 8/9) and a cross-check on execution counts.

Durations here are integer microseconds throughout. The published
section_durations.csv was produced with pandas `.microseconds`, which is the
sub-second REMAINDER: 3 of its 125 rows wrapped, e.g. 1,299,137 us recorded as
299,137, and high-effort runs wrap more because more of their intervals cross
1 s. Working in integers makes that class of error unrepresentable rather than
merely fixed.
"""
import argparse, csv, gzip, hashlib, json, os, re, sys
from collections import Counter, defaultdict

# ---------------------------------------------------------------- EXEC record

EXEC_INT = {
    "pipeline", "module_index", "param_id", "num_images", "batch_bytes",
    "cached", "latency_pred_us", "latency_meas_us", "energy_pred_uwh",
    "deadline_us", "slack_us", "ingest_depth", "partial_depth",
    "rss_kb", "peak_rss_kb", "child_peak_rss_kb",
    "io_read_bytes", "io_write_bytes",
}
EXEC_FLOAT = {"energy_debited_uwh", "battery_wh"}

# latency_pred_us / energy_pred_uwh are int32_t printed with %u
# (module_config.pb-c.h:62-63). A negative cost surfaces as ~4.29e9 rather than
# as a negative number, so flag it instead of ingesting it.
U32_WRAP_THRESHOLD = 2 ** 31


def parse_exec_line(line):
    """`EXEC k=v k=v ...` -> dict. Values are bare tokens, so a split suffices."""
    rec, warnings = {}, []
    for tok in line.split()[1:]:
        if "=" not in tok:
            continue
        k, _, v = tok.partition("=")
        if k in EXEC_INT:
            try:
                rec[k] = int(v)
            except ValueError:
                warnings.append(f"non-integer {k}={v}")
        elif k in EXEC_FLOAT:
            try:
                rec[k] = float(v)
            except ValueError:
                warnings.append(f"non-float {k}={v}")
        else:
            rec[k] = v
    for k in ("latency_pred_us", "energy_pred_uwh"):
        if rec.get(k, 0) >= U32_WRAP_THRESHOLD:
            warnings.append(f"{k}={rec[k]} looks like a negative int32 printed as %u")
    return rec, warnings


# ------------------------------------------------------- other daemon markers
# Several of these are prose, not key=value, and QUEUE_CORRUPT has three shapes.
MARKERS = {
    "queue_select":      re.compile(r"^QUEUE_SELECT queue=(\S+) uuid=(\S+) progress=(-?\d+)"),
    "queue_remove":      re.compile(r"^QUEUE_REMOVE queue=(\S+) reason=(\S+) expect_uuid=(\S+) removed_uuid=(\S+)"),
    "queue_mismatch":    re.compile(r"^QUEUE_MISMATCH "),
    "ingest_reject":     re.compile(r"^INGEST_REJECT "),
    "queue_corrupt":     re.compile(r"^QUEUE_CORRUPT "),
    "queue_rollback":    re.compile(r"^QUEUE_ROLLBACK "),
    "queue_sync_fail":   re.compile(r"^QUEUE_SYNC_FAIL "),
    "queue_align_warn":  re.compile(r"^QUEUE_ALIGN_WARN "),
    "orphan_remove":     re.compile(r"^ORPHAN_REMOVE "),
    "startup_queues":    re.compile(r"^STARTUP_QUEUES ingest=(\d+) partial=(\d+)"),
    "boot":              re.compile(r"^SIPP build: (.+)$"),
    "completed":         re.compile(r"^Pipeline fully executed successfully uuid=(\S+)"),
    "partial":           re.compile(r"^Pipeline partially executed successfully uuid=(\S+)"),
    "queue_full":        re.compile(r"^Priority queue is full"),
    "missing_file":      re.compile(r"^ERROR: batch \S+ references missing file"),
    "short_file":        re.compile(r"^ERROR: batch \S+ file .* bytes, expected"),
    "trace_dropped":     re.compile(r"^WARNING: (\d+) trace events were dropped"),
}


def parse_console(path):
    execs, warns = [], []
    counts = Counter()
    builds, completed, removes = [], set(), Counter()
    dropped = 0
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("EXEC "):
                rec, w = parse_exec_line(line)
                execs.append(rec)
                warns.extend(w)
                continue
            for name, rx in MARKERS.items():
                m = rx.match(line)
                if not m:
                    continue
                counts[name] += 1
                if name == "boot":
                    builds.append(m.group(1))
                elif name == "completed":
                    completed.add(m.group(1))
                elif name == "queue_remove":
                    removes[m.group(2)] += 1
                elif name == "trace_dropped":
                    dropped = max(dropped, int(m.group(1)))
                break
    return {
        "executions": execs, "counts": counts, "builds": builds,
        "completed_uuids": completed, "removes_by_reason": removes,
        "warnings": warns, "trace_dropped_events": dropped,
    }


# ------------------------------------------------------------------- trace

TRACE_HEADER = '{"traceEvents":['
OLD_BUFFER_CAP = 1_000_000  # the fixed cap before MTR_BUFFER_EVENTS


def parse_trace(path):
    """Read a Chrome trace, tolerating the two defects in the archived ones.

    Archived traces carry the header ~501 times -- one per forked child that
    flushed its inherited stdio buffer on exit() -- so they are not valid JSON
    until de-duplicated. And one is silently truncated at exactly the old
    1,000,000-event cap with nothing in the file to say so.
    """
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", errors="replace") as fh:
        raw = fh.read()

    headers = raw.count(TRACE_HEADER)
    flags = []
    if headers > 1:
        flags.append(f"duplicate_headers={headers}")
        first = raw.index(TRACE_HEADER)
        body = raw[first + len(TRACE_HEADER):]
        body = body.replace(TRACE_HEADER, "")
        raw = TRACE_HEADER + body
    elif headers == 0:
        flags.append("no_header")

    try:
        events = json.loads(raw)["traceEvents"]
    except Exception:
        # A trace cut by SIGKILL has no closing "]}"; salvage whole lines.
        events = []
        for ln in raw.splitlines():
            ln = ln.strip().rstrip(",")
            if ln.startswith("{") and ln.endswith("}"):
                try:
                    events.append(json.loads(ln))
                except Exception:
                    pass
        flags.append("unterminated_recovered")

    if len(events) >= OLD_BUFFER_CAP:
        flags.append(f"truncated_at_cap={len(events)}")

    build = None
    for e in events:
        if e.get("ph") == "M" and e.get("name") == "process_name":
            build = e.get("args", {}).get("name")
            break

    # Pair B/E per (pid, tid, name). Durations are integer microseconds.
    stacks, spans = defaultdict(list), []
    for e in events:
        ph = e.get("ph")
        if ph == "B":
            stacks[(e.get("pid"), e.get("tid"), e.get("name"))].append(e)
        elif ph == "E":
            st = stacks.get((e.get("pid"), e.get("tid"), e.get("name")))
            if st:
                b = st.pop()
                spans.append({
                    "name": e.get("name"),
                    # The heuristic arms cannot be told apart by name:
                    # lowest_effort_heuristic.c emits events NAMED
                    # "best_effort_heuristic". Only the category discriminates.
                    "cat": b.get("cat"),
                    "ts_us": b.get("ts"),
                    "dur_us": e.get("ts", 0) - b.get("ts", 0),
                })
    unclosed = sum(len(v) for v in stacks.values())
    if unclosed:
        # Expected == 1 for a cleanly stopped run: the outer process_images_loop
        # BEGIN never gets its END.
        flags.append(f"unclosed_spans={unclosed}")

    return {
        "events": len(events), "spans": spans, "build": build,
        "flags": flags, "phases": Counter(e.get("ph") for e in events),
    }


# -------------------------------------------------------------------- driver

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def collect(run_dir=None, console=None, trace=None):
    meta = {}
    if run_dir:
        rj = os.path.join(run_dir, "RUN.json")
        if os.path.exists(rj):
            meta = json.load(open(rj))
        console = console or _first(run_dir, "console.log", "console.log.gz")
        trace = trace or _first(run_dir, "trace.json", "trace.json.gz")
        meta.setdefault("run_id", os.path.basename(os.path.normpath(run_dir)))
    meta.setdefault("run_id", os.path.basename(console or trace or "unknown"))

    c = parse_console(console) if console and os.path.exists(console) else None
    t = parse_trace(trace) if trace and os.path.exists(trace) else None

    row = dict(meta)
    if c:
        row["n_executions"] = len(c["executions"])
        row["n_batches"] = len({e.get("uuid") for e in c["executions"] if e.get("uuid")})
        row["n_completed_unique"] = len(c["completed_uuids"])
        row["n_boots"] = len(c["builds"])
        row["build_id_from_log"] = c["builds"][0] if c["builds"] else None
        row["trace_dropped_events"] = c["trace_dropped_events"]
        for k, v in c["counts"].items():
            row[f"count_{k}"] = v
        for k, v in c["removes_by_reason"].items():
            row[f"removed_{k}"] = v
        if c["executions"]:
            row["io_write_bytes_total"] = max(
                e.get("io_write_bytes", 0) for e in c["executions"])
            row["child_peak_rss_kb"] = max(
                e.get("child_peak_rss_kb", 0) for e in c["executions"])
            row["peak_rss_kb"] = max(e.get("peak_rss_kb", 0) for e in c["executions"])
        row["parse_warnings"] = "; ".join(sorted(set(c["warnings"]))) or ""
        if console:
            row["console_sha256"] = sha256(console)
    if t:
        row["trace_events"] = t["events"]
        row["trace_flags"] = ";".join(t["flags"])
        row["trace_build"] = t["build"]
        if trace:
            row["trace_sha256"] = sha256(trace)
    return row, (c["executions"] if c else []), (t["spans"] if t else [])


def _first(d, *names):
    for n in names:
        p = os.path.join(d, n)
        if os.path.exists(p):
            return p
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rundirs", nargs="*")
    ap.add_argument("--console")
    ap.add_argument("--trace")
    ap.add_argument("-o", "--outdir", default="analysis/parsed")
    a = ap.parse_args()

    jobs = [(d, None, None) for d in a.rundirs] or [(None, a.console, a.trace)]
    run_rows, exec_rows = [], []
    for rd, con, tr in jobs:
        row, execs, _spans = collect(rd, con, tr)
        run_rows.append(row)
        for e in execs:
            e = dict(e)
            e["run_id"] = row["run_id"]
            exec_rows.append(e)
        flags = row.get("trace_flags") or ""
        warn = row.get("parse_warnings") or ""
        print(f"{row['run_id']}: {row.get('n_executions', 0)} executions, "
              f"{row.get('n_batches', 0)} batches, "
              f"{row.get('trace_events', 0)} trace events"
              + (f"  FLAGS[{flags}]" if flags else "")
              + (f"  WARN[{warn}]" if warn else ""))

    os.makedirs(a.outdir, exist_ok=True)
    _write_csv(os.path.join(a.outdir, "executions.csv"), exec_rows)
    _write_csv(os.path.join(a.outdir, "runs.csv"), run_rows)
    print(f"\nwrote {a.outdir}/executions.csv ({len(exec_rows)} rows) "
          f"and runs.csv ({len(run_rows)} rows)")
    return 0


def _write_csv(path, rows):
    if not rows:
        open(path, "w").close()
        return
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    sys.exit(main())
