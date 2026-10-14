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
    batches.csv     one row per ingested (or dropped) batch: ingest, deadline,
                    class, completion, latency, deadline met, blocking, halts
    battery.csv     the battery timeline: BATTERY / BATTERY_FF / BATTERY_SET /
                    BOARD_POWER lines
    events.csv      scheduler and harness events: SCHED_STATE / SCHED_HALT /
                    SCHED_RESUME / MARK / INGEST_DROP / BATTERY_EMPTY

Stage 5a lines are key=value (src/battery_simulator.c, dipp_process.c,
pipeline_executor.c) and are read by one tokenizer. A trace run's feed.log
(tests/board/feed_trace.sh) gives each frame's class and datatake: the feeder
sends `dipp_ctl mark <scene>` immediately before the camera, and both reach
the daemon in send order, so the n-th MARK belongs to the n-th INGEST (or
INGEST_DROP) however the two interleave. The daemon reads its queue -- data
and control alike -- only between batches, so neither INGEST nor MARK
carries the arrival time. The camera does: deadline = send time + SLO, so a
frame ARRIVES at deadline_us - slo_s (slo_s from feed.log), and latency,
blocking and waiting are measured from there; ingest_delay is arrival ->
INGEST. Its simulated arrival time is the feeder's sim_now_s. Without a
feed.log (populate / poisson loads) arrival is the INGEST.

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
    "ts_us",
    "pipeline", "module_index", "param_id", "num_images", "batch_bytes",
    "cached", "n_input", "latency_pred_us", "latency_meas_us", "energy_pred_uwh",
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


# ------------------------------------------------------ Stage 5a key=value
KV_TAGS = {
    "BATTERY", "BATTERY_FF", "BATTERY_SET", "BATTERY_CONFIG", "BATTERY_EMPTY",
    "BATTERY_MARGIN_ABOVE_CAPACITY", "BOARD_POWER", "INGEST", "INGEST_DROP",
    "MARK", "COST_MODEL", "COST_OBSERVE", "COST_STORE_INIT", "COST_STORE_RESET",
    "SCHED_STATE", "SCHED_HALT", "SCHED_RESUME", "CTL_STALE_DISCARDED",
    "SCHED_CONFIG", "SCHED_TIER", "PREEMPT", "COST_FLAG",
}
BATTERY_TAGS = ("BATTERY", "BATTERY_FF", "BATTERY_SET", "BOARD_POWER")
EVENT_TAGS = ("SCHED_STATE", "SCHED_HALT", "SCHED_RESUME", "MARK", "INGEST_DROP", "BATTERY_EMPTY",
              "SCHED_TIER", "PREEMPT", "COST_FLAG")
COMPLETION_RX = re.compile(r"^Pipeline fully executed successfully uuid=(\S+) ts_us=(\d+) deadline_us=(-?\d+)")
UPLOAD_BATCH_RX = re.compile(r"^Uploading batch size of (\d+) bytes")
UPLOAD_IMAGE_RX = re.compile(r"^Uploading image \d+: metadata size = \d+, image size = \d+")


def _num(v):
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def parse_kv(line):
    """`TAG k=v k=v ... [-- prose]` -> (TAG, {k: number-or-str})."""
    head, _, _ = line.partition(" -- ")
    toks = head.split()
    return toks[0], {k: _num(v) for k, _, v in (t.partition("=") for t in toks[1:]) if v != "" or "=" in t}


def parse_stage5(console, feedlog=None):
    kv = []                    # (tag, dict) in file order
    completions = {}
    uploads = {}               # uuid -> [bytes, tiles]: upload() runs right after the completion line
    last_done = None
    opener = gzip.open if str(console).endswith(".gz") else open
    with opener(console, "rt", errors="replace") as fh:
        for line in fh:
            tag = line.split(" ", 1)[0]
            if tag in KV_TAGS:
                kv.append(parse_kv(line.rstrip("\n")))
            elif tag == "Pipeline":
                m = COMPLETION_RX.match(line)
                if m:
                    completions[m.group(1)] = int(m.group(2))
                    last_done = m.group(1)
            elif tag == "Uploading" and last_done is not None:
                m = UPLOAD_BATCH_RX.match(line)
                if m:
                    uploads[last_done] = [int(m.group(1)), 0]
                elif UPLOAD_IMAGE_RX.match(line) and last_done in uploads:
                    uploads[last_done][1] += 1
    feed, ff, trace_end = {}, [], {}
    if feedlog and os.path.exists(feedlog):
        for line in open(feedlog, errors="replace"):
            tag, d = parse_kv(line.rstrip("\n"))
            if tag == "FEED":
                feed[d.get("scene")] = d
            elif tag == "FF":
                ff.append(d)
            elif tag == "TRACE_END":
                trace_end = d
    return kv, completions, feed, ff, trace_end, uploads


def stage5_tables(run_id, kv, completions, feed, execs, uploads=None):
    """batches, battery rows, event rows, and the run-level summary."""
    by_uuid = defaultdict(list)
    for e in execs:
        by_uuid[e.get("uuid")].append(e)
    # Execution intervals (EXEC is printed when the module returns).
    spans = sorted((e["ts_us"] - e.get("latency_meas_us", 0), e["ts_us"], e.get("uuid"))
                   for e in execs if "ts_us" in e)

    batches, battery, events = [], [], []
    marks = [d for t, d in kv if t == "MARK"]      # n-th MARK <-> n-th INGEST/INGEST_DROP
    n_in = 0
    # deadline window (s) -> frame class, from the feeder's own log (one class per window)
    slo_class = {}
    for f_ in feed.values():
        if f_.get("slo_s") is not None and f_.get("class"):
            slo_class.setdefault(int(f_["slo_s"]), f_["class"])
    halts = defaultdict(lambda: [0, 0.0])
    for tag, d in kv:
        if tag in BATTERY_TAGS:
            battery.append(dict(run_id=run_id, tag=tag, **d))
        if tag in EVENT_TAGS:
            events.append(dict(run_id=run_id, tag=tag, **d))
        if tag == "SCHED_HALT":
            halts[d.get("uuid")][0] += 1
        elif tag == "SCHED_RESUME":
            halts[d.get("uuid")][1] += float(d.get("halted_s", 0))
        elif tag in ("INGEST", "INGEST_DROP"):
            u, t_in = d.get("uuid"), d.get("ts_us")
            row = {"run_id": run_id, "uuid": u, "pipeline": d.get("pipeline"),
                   "num_images": d.get("num_images"), "ingest_ts_us": t_in,
                   "deadline_us": d.get("deadline_us"), "slo_us": d.get("slo_us"),
                   "dropped": int(tag == "INGEST_DROP"), "ingest_depth": d.get("ingest_depth")}
            t_arr = t_in
            if n_in < len(marks):
                mk = marks[n_in]
                sc = mk.get("scene")
                row.update(scene=sc, mark_sim_t_s=mk.get("sim_t_s"), mark_phase=mk.get("phase"))
                f = feed.get(sc)
                if f:
                    row.update(cls=f.get("class"), datatake=f.get("datatake"), slo_s=f.get("slo_s"),
                               late_s=f.get("late_s"), arrival_sim_t_s=f.get("sim_now_s"))
                    if d.get("deadline_us") is not None and f.get("slo_s") is not None:
                        t_arr = d["deadline_us"] - int(f["slo_s"]) * 1_000_000
            n_in += 1
            # From policy v2 on, frames carry their capture time (INGEST capture_us):
            # the arrival time itself, not reconstructed from deadline - SLO.
            if d.get("capture_us") is not None:
                t_arr = int(d["capture_us"])
                row["arrival_src"] = "capture"
                # The frame's own window (deadline - capture) identifies its
                # class; the n-th FEED <-> n-th INGEST pairing can swap two
                # frames ingested out of feed order (2 of 4,048 frames in eval2).
                if d.get("deadline_us") is not None and slo_class:
                    w = round((int(d["deadline_us"]) - t_arr) / 1e6)
                    if w in slo_class:
                        if row.get("cls") is not None and row["cls"] != slo_class[w]:
                            row["cls_from_feed"] = row["cls"]
                        row["cls"], row["slo_s"] = slo_class[w], w
            else:
                row["arrival_src"] = "deadline-slo" if t_arr != t_in else "ingest"
            row["arrival_ts_us"] = t_arr
            row["ingest_delay_us"] = t_in - t_arr
            ex = by_uuid.get(u, [])
            row["n_exec"] = len(ex)
            row["exec_us_sum"] = sum(e.get("latency_meas_us", 0) for e in ex)
            row["energy_debited_uwh"] = round(sum(e.get("energy_debited_uwh", 0.0) for e in ex), 3)
            if ex:
                first_start = min(e["ts_us"] - e.get("latency_meas_us", 0) for e in ex)
                row["wait_us"] = first_start - t_arr
            # Non-preemptive blocking: what was left of another batch's
            # execution already running when this one arrived.
            row["blocking_us"] = max([end - t_arr for st, end, uu in spans if uu != u and st < t_arr < end] or [0])
            done = completions.get(u)
            row["completed"] = int(done is not None)
            if done is not None:
                row["completion_ts_us"] = done
                row["latency_us"] = done - t_arr
                row["deadline_met"] = int(done <= d.get("deadline_us", done))
            row["n_halts"], row["halted_s"] = halts[u][0], round(halts[u][1], 3)
            up = (uploads or {}).get(u)
            row["downlink_bytes"] = up[0] if up else None
            row["tiles_delivered"] = up[1] if up else None
            batches.append(row)
    # SCHED_HALT can precede INGEST of later batches; fill halts afterwards.
    for row in batches:
        row["n_halts"], row["halted_s"] = halts[row["uuid"]][0], round(halts[row["uuid"]][1], 3)

    summ = {}
    if batches:
        def q(v, p):
            v = sorted(v)
            return v[min(len(v) - 1, int(round(p * (len(v) - 1))))] if v else None
        summ["batches_ingested"] = sum(1 for b in batches if not b["dropped"])
        summ["batches_dropped"] = sum(b["dropped"] for b in batches)
        summ["batches_completed"] = sum(b["completed"] for b in batches)
        summ["batches_unfinished"] = summ["batches_ingested"] - summ["batches_completed"]
        for cls in ("urgent", "routine", None):
            bs = [b for b in batches if (cls is None or b.get("cls") == cls) and not b["dropped"]]
            if not bs:
                continue
            k = cls or "all"
            lat = [b["latency_us"] / 1e6 for b in bs if b["completed"]]
            summ[f"{k}_n"] = len(bs)
            # A batch that never completed missed its deadline.
            summ[f"{k}_met_frac"] = round(sum(b.get("deadline_met", 0) for b in bs) / len(bs), 4)
            summ[f"{k}_latency_p50_s"] = q(lat, 0.5)
            summ[f"{k}_latency_p95_s"] = q(lat, 0.95)
            summ[f"{k}_blocking_max_s"] = max(b["blocking_us"] for b in bs) / 1e6
        summ["halts"] = sum(1 for t, _ in kv if t == "SCHED_HALT")
        summ["halted_s_total"] = round(sum(float(d.get("halted_s", 0)) for t, d in kv if t == "SCHED_RESUME"), 3)
    wh = [d["wh"] for t, d in kv if t == "BATTERY" and "wh" in d] + \
         [d["wh_after"] for t, d in kv if t == "BATTERY_FF" and "wh_after" in d]
    if wh:
        summ["battery_wh_min"] = min(wh)
    summ["battery_empty"] = sum(1 for t, _ in kv if t == "BATTERY_EMPTY")
    cfg = [d for t, d in kv if t == "BATTERY_CONFIG"]
    if cfg:
        summ.update({f"cfg_{k}": cfg[0][k] for k in ("margin_wh", "soc0", "phase0", "board_mode") if k in cfg[0]})
    bw = [d.get("board_wh") for t, d in kv if t in ("BATTERY", "BOARD_POWER") and "board_wh" in d]
    if bw:
        summ["board_wh_last"] = bw[-1]
    summ["cost_src_hint"] = sum(1 for e in execs if e.get("cost_src") == "hint")
    summ["preemptions"] = sum(1 for t, _ in kv if t == "PREEMPT")
    summ["cost_flags"] = sum(1 for t, _ in kv if t == "COST_FLAG")
    sc = [d for t, d in kv if t == "SCHED_CONFIG"]
    if sc:
        summ.update({f"sched_{k}": sc[0][k] for k in ("q_med", "q_low", "granularity", "gate_med_ms", "gate_low_ms")
                     if k in sc[0]})
    return batches, battery, events, summ


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


def collect(run_dir=None, console=None, trace=None, feedlog=None):
    meta = {}
    if run_dir:
        rj = os.path.join(run_dir, "RUN.json")
        if os.path.exists(rj):
            meta = json.load(open(rj))
        console = console or _first(run_dir, "console.log", "console.log.gz")
        trace = trace or _first(run_dir, "trace.json", "trace.json.gz")
        feedlog = feedlog or _first(run_dir, "feed.log", "feed.log.gz")
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
    s5 = ([], [], [])
    if c and console:
        kv, comp, feed, _ff, tend, ups = parse_stage5(console, feedlog)
        batches, battery, events, summ = stage5_tables(row["run_id"], kv, comp, feed, c["executions"], ups)
        row.update(summ)
        row.update({f"feed_{k}": v for k, v in tend.items()})
        s5 = (batches, battery, events)
    if t:
        row["trace_events"] = t["events"]
        row["trace_flags"] = ";".join(t["flags"])
        row["trace_build"] = t["build"]
        if trace:
            row["trace_sha256"] = sha256(trace)
    return row, (c["executions"] if c else []), (t["spans"] if t else []), s5


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
    ap.add_argument("--feed-log")
    ap.add_argument("-o", "--outdir", default="analysis/parsed")
    a = ap.parse_args()

    jobs = [(d, None, None, None) for d in a.rundirs] or [(None, a.console, a.trace, a.feed_log)]
    run_rows, exec_rows, batch_rows, batt_rows, event_rows = [], [], [], [], []
    for rd, con, tr, fl in jobs:
        row, execs, _spans, (b5, bt5, ev5) = collect(rd, con, tr, fl)
        batch_rows += b5; batt_rows += bt5; event_rows += ev5
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
    _write_csv(os.path.join(a.outdir, "batches.csv"), batch_rows)
    _write_csv(os.path.join(a.outdir, "battery.csv"), batt_rows)
    _write_csv(os.path.join(a.outdir, "events.csv"), event_rows)
    print(f"\nwrote {a.outdir}/executions.csv ({len(exec_rows)} rows), runs.csv ({len(run_rows)}), "
          f"batches.csv ({len(batch_rows)}), battery.csv ({len(batt_rows)}), events.csv ({len(event_rows)})")
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
