#!/usr/bin/env python3
"""Regression tests for analysis/parse_runs.py and analysis/tables.py.

    tests/test_parse_runs.py

The archived broken traces are the test corpus, which is why they are worth
keeping: every case here is a defect that reached published output once.
Tests whose fixture is absent are SKIPped, so this runs anywhere.
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "analysis"))

import parse_runs  # noqa: E402
import tables  # noqa: E402

ARCHIVE = os.path.expanduser("~/dipp2-experiments/traces")
FIX_STAGE4 = os.path.join(ROOT, "analysis", "stage4")
FIX_STAGE2 = os.path.join(ROOT, "analysis", "stage2-verification",
                          "trace-20batches.json.gz")
CONFIG = os.path.join(ROOT, "experiments", "board", "flight-config")

RESULTS = []


def check(name, fn):
    fixture = fn.__doc__ or ""
    try:
        r = fn()
    except FileNotFoundError as e:
        RESULTS.append(("SKIP", name, str(e)))
        return
    except AssertionError as e:
        RESULTS.append(("FAIL", name, str(e)))
        return
    RESULTS.append(("PASS", name, r or ""))


def _trace(events):
    doc = {"traceEvents": events, "displayTimeUnit": "ms"}
    fd, p = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(doc, fh)
    return p


# --------------------------------------------------------------- the tests --

def t_duration_is_total_not_remainder():
    """A span longer than one second keeps its whole microsecond count."""
    p = _trace([{"ph": "B", "pid": 1, "tid": 1, "name": "x", "ts": 0, "cat": "c"},
                {"ph": "E", "pid": 1, "tid": 1, "name": "x", "ts": 1299137}])
    try:
        t = parse_runs.parse_trace(p)
    finally:
        os.unlink(p)
    got = t["spans"][0]["dur_us"]
    # The published section_durations.csv used pandas .microseconds -- the
    # sub-second remainder -- and recorded this exact span as 299137.
    assert got == 1299137, f"got {got}, want 1299137 (the .microseconds bug gives 299137)"
    return "1,299,137 us, not 299,137"


def t_header_dedup():
    """501 concatenated headers de-duplicate into one event stream."""
    p = os.path.join(ARCHIVE, "poisson-mmap-100-3i-15slo-5qt-adaptive.json")
    if not os.path.exists(p):
        raise FileNotFoundError(p)
    t = parse_runs.parse_trace(p)
    dup = [f for f in t["flags"] if f.startswith("duplicate_headers=")]
    assert dup, f"duplicate headers not flagged; flags={t['flags']}"
    assert t["events"] > 0, "de-duplicated stream is empty"
    return f"{dup[0]}, {t['events']} events"


def t_truncation_detected():
    """A trace cut at the old 1,000,000-event cap is flagged, not reported whole."""
    cands = [f for f in os.listdir(ARCHIVE)] if os.path.isdir(ARCHIVE) else []
    hit = [f for f in cands if "static-low" in f and f.endswith(".json")]
    if not hit:
        raise FileNotFoundError(f"no static-low trace in {ARCHIVE}")
    t = parse_runs.parse_trace(os.path.join(ARCHIVE, hit[0]))
    trunc = [f for f in t["flags"] if f.startswith("truncated_at_cap=")]
    assert trunc, f"truncation not flagged; flags={t['flags']}"
    return trunc[0]


def t_known_good_run_counts():
    """The 20-batch pipeline-2 run parses to exactly 100 executions."""
    if not os.path.exists(os.path.join(FIX_STAGE4, "console.log.gz")):
        raise FileNotFoundError(FIX_STAGE4)
    row, execs, _ = parse_runs.collect(FIX_STAGE4)
    assert row["n_executions"] == 100, f"n_executions={row['n_executions']}, want 100"
    assert row["n_batches"] == 20, f"n_batches={row['n_batches']}, want 20"
    assert len(execs) == 100
    mods = {e["module"] for e in execs}
    assert len(mods) == 5, f"want 5 distinct modules, got {sorted(mods)}"
    return "100 executions, 20 batches, 5 modules"


def t_stage2_trace_clean():
    """The known-good Stage 2 trace has one header and no drops."""
    if not os.path.exists(FIX_STAGE2):
        raise FileNotFoundError(FIX_STAGE2)
    t = parse_runs.parse_trace(FIX_STAGE2)
    assert not [f for f in t["flags"] if f.startswith("duplicate_headers")], t["flags"]
    assert not [f for f in t["flags"] if f.startswith("truncated")], t["flags"]
    assert t["events"] == 1456, f"events={t['events']}, want 1456"
    return f"{t['events']} events, flags={t['flags'] or 'none'}"


def t_effort_map_off_by_one():
    """Config slot N is daemon param_id N-1, and the labels follow."""
    if not os.path.isdir(CONFIG):
        raise FileNotFoundError(CONFIG)
    m = tables.load_effort_map(CONFIG)
    if not m:
        raise FileNotFoundError("PyYAML unavailable")
    # Pipeline 2 runs slots 1,2,3,6,11 -> daemon indices 0,1,2,5,10.
    for slot, want_stage in ((1, "libdemosaic"), (2, "libtile"),
                             (3, "libtflite_classification"),
                             (6, "libtflite_binary_segmentation"),
                             (11, "libjpegxl_module")):
        stage, effort, _order, got_slot = m[slot - 1]
        assert stage == want_stage, f"index {slot-1}: {stage} != {want_stage}"
        assert got_slot == slot, f"index {slot-1}: slot {got_slot} != {slot}"
    # A stage with several implementations must keep its real effort label,
    # not the "default" that single-implementation pipelines give the same id.
    assert m[2][1] == "low", f"slot 3 effort {m[2][1]!r}, want 'low'"
    assert m[4][1] == "high", f"slot 5 effort {m[4][1]!r}, want 'high'"
    return "slots 1,2,3,6,11 -> indices 0,1,2,5,10 with effort labels intact"


def t_warmup_discard():
    """Exactly the first batch of each run is discarded."""
    if not os.path.exists(os.path.join(FIX_STAGE4, "console.log.gz")):
        raise FileNotFoundError(FIX_STAGE4)
    _row, execs, _ = parse_runs.collect(FIX_STAGE4)
    for e in execs:
        e["run_id"] = "r"
    kept, dropped = tables.drop_warmup(execs)
    assert dropped == 5, f"dropped {dropped} executions, want 5 (one 5-module batch)"
    assert len({e["uuid"] for e in kept}) == 19
    return "5 executions (1 batch) discarded, 19 batches kept"


def t_negative_cost_flagged():
    """An int32 cost printed with %u must be flagged, not ingested."""
    line = ("EXEC ts_us=1 uuid=u pipeline=2 module_index=0 module=m param_id=0 "
            "num_images=1 batch_bytes=10 cached=0 latency_pred_us=4294967295 "
            "latency_meas_us=5 energy_pred_uwh=1 energy_debited_uwh=1.0 "
            "battery_wh=1.0 deadline_us=1 slack_us=1 ingest_depth=0 "
            "partial_depth=0 heuristic=BEST_EFFORT rss_kb=1 peak_rss_kb=1 "
            "child_peak_rss_kb=1 io_read_bytes=1 io_write_bytes=1")
    rec, warnings = parse_runs.parse_exec_line(line)
    assert rec["ts_us"] == 1, "ts_us not parsed as an integer"
    assert any("latency_pred_us" in w for w in warnings), (
        f"4294967295 us (a negative int32) not flagged; warnings={warnings}")
    return "; ".join(warnings)


def main():
    for name, fn in sorted((k[2:], v) for k, v in globals().items()
                           if k.startswith("t_") and callable(v)):
        check(name, fn)
    width = max(len(n) for _, n, _ in RESULTS)
    for status, name, detail in RESULTS:
        print(f"{status:4}  {name:<{width}}  {detail}")
    failed = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    skipped = sum(1 for s, _, _ in RESULTS if s == "SKIP")
    print(f"\n{len(RESULTS) - failed - skipped} passed, {failed} failed, "
          f"{skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
