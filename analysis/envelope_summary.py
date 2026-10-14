#!/usr/bin/env python3
"""Summarise tools/envelope_sampler.sh output into the operating-envelope
numbers (R1.5): peak and median memory of SIPP's processes, peak shared
memory, peak persisted bytes and files, minimum available memory.

    python3 analysis/envelope_summary.py envelope.txt [--board-json runs/<set>/<label>/board.json]

With --board-json, the run's queue capacities context (frames fed, dropped)
is printed next to it. Prints a markdown table; writes nothing.
"""
import argparse, json, statistics, sys

COLS = ("t_s", "pss_kb_dipp", "n_dipp", "shm_bytes", "shm_segs", "data_bytes", "data_files", "mem_avail_kb")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("samples")
    ap.add_argument("--board-json")
    a = ap.parse_args()
    rows = []
    for ln in open(a.samples):
        if ln.startswith("#") or not ln.strip():
            continue
        v = ln.split()
        if len(v) != len(COLS):
            sys.exit(f"{a.samples}: malformed line {ln!r}")
        rows.append(dict(zip(COLS, map(float, v))))
    if not rows:
        sys.exit(f"{a.samples}: no samples")
    busy = [r for r in rows if r["n_dipp"] > 0]
    if not busy:
        sys.exit(f"{a.samples}: no sample saw a dipp process")
    mb = lambda x: f"{x / 1024:.1f} MB"
    print(f"samples {len(rows)} over {rows[-1]['t_s'] - rows[0]['t_s']:.0f} s ({len(busy)} with SIPP running)\n")
    print("| quantity | median | peak |\n|---|---:|---:|")
    print(f"| SIPP memory (Pss, daemon + module processes) | {mb(statistics.median(r['pss_kb_dipp'] for r in busy))} | {mb(max(r['pss_kb_dipp'] for r in busy))} |")
    print(f"| SIPP processes | {statistics.median(r['n_dipp'] for r in busy):.0f} | {max(r['n_dipp'] for r in busy):.0f} |")
    print(f"| shared memory (frames in flight) | {mb(statistics.median(r['shm_bytes'] for r in busy) / 1024)} | {mb(max(r['shm_bytes'] for r in busy) / 1024)} |")
    print(f"| persisted data | {mb(statistics.median(r['data_bytes'] for r in busy) / 1024)} | {mb(max(r['data_bytes'] for r in busy) / 1024)} |")
    print(f"| persisted files | {statistics.median(r['data_files'] for r in busy):.0f} | {max(r['data_files'] for r in busy):.0f} |")
    print(f"| board memory available (minimum) | -- | {mb(min(r['mem_avail_kb'] for r in rows))} |")
    if a.board_json:
        b = json.load(open(a.board_json))
        keys = [k for k in b if any(s in k for s in ("fed", "drop", "completed", "label", "trace"))]
        print("\nrun:", {k: b[k] for k in keys})
    return 0


if __name__ == "__main__":
    sys.exit(main())
