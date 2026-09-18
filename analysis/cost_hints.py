#!/usr/bin/env python3
"""Measured latency_cost / energy_cost for the flight config.

    analysis/cost_hints.py --enc analysis/stage4.5-models/dev_j2k_rungs.json

The scheduler picks an effort level by comparing each candidate's predicted cost
against the time and energy it has left, so these two numbers per slot are the
entire input to that decision. Every one of them in the committed flight config
is a placeholder -- the monolith's are 10 us and 5 uWh against true values in
the hundreds of thousands, which is three to five orders of magnitude low and is
why pipeline 4 scheduled the way it did in the published figures.

Energy is a DIFFERENCE and the runs are not the same length, so it is computed
as the work-only integral

    E_work = (E_run - P_idle * T_run) - (E_base - P_idle * T_base)

rather than E_run - E_base. Subtracting the raw readings charges a slow module
for the idle seconds it was merely present for: a 87.5 s encoder run against a
55.9 s baseline carries 31.6 extra seconds of the ~2.5 W board floor, which is
0.022 Wh -- larger than most of the module energies being measured. P_idle comes
from `bench_models.py --idle`; without it this refuses to emit energy rather
than emitting a number that is mostly board floor.
"""

import argparse
import json
import sys

TILES = 99          # tiles per frame, from the 2464x2056 -> 224x224 tiling


def work_energy_wh(run, base, p_idle):
    """Energy attributable to the stage under test, idle floor removed."""
    er, tr = run["energy"].get("energy_wh"), run["energy"].get("measured_s")
    eb, tb = base["energy"].get("energy_wh"), base["energy"].get("measured_s")
    if None in (er, tr, eb, tb):
        return None
    return (er - p_idle * tr / 3600.0) - (eb - p_idle * tb / 3600.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--enc", required=True, help="device sweep JSON")
    ap.add_argument("--idle-from", help="JSON carrying the idle measurement "
                                        "(defaults to --enc)")
    ap.add_argument("--tiles", type=int, default=TILES)
    a = ap.parse_args()

    d = json.load(open(a.enc))
    idle_src = json.load(open(a.idle_from)) if a.idle_from else d
    idle = idle_src.get("idle", {}).get("energy", {}).get("avg_power_w")
    base = d.get("baseline")

    print(f"{'config':22} {'ms/tile':>9} {'B/tile':>9} {'uWh/tile':>10}")
    if idle is None:
        print("  (no idle measurement: run bench_models.py --idle 60; "
              "energy withheld rather than reported as board floor)")
    if base is None:
        print("  (no baseline run in this file: energy needs one)")

    for k, v in d.items():
        if k in ("baseline", "idle"):
            continue
        lat = v.get("classify_us") or []
        ms = (sorted(lat)[len(lat) // 2] / 1000.0 / a.tiles) if lat else float("nan")
        b = v.get("encoded_mean_bytes", float("nan"))
        uwh = float("nan")
        if idle is not None and base is not None:
            e = work_energy_wh(v, base, idle)
            if e is not None:
                uwh = e * 1e6 / a.tiles
        print(f"{k:22} {ms:9.2f} {b:9.0f} {uwh:10.1f}")
        # The daemon's hints are per EXECUTION (a whole frame), not per tile.
        if lat:
            print(f"{'':22}   -> latency_cost: {int(sorted(lat)[len(lat)//2])}"
                  + (f"   energy_cost: {int(uwh * a.tiles)}"
                     if uwh == uwh else "   energy_cost: (needs idle)"))


if __name__ == "__main__":
    main()
