#!/usr/bin/env python3
"""Write the measured per-execution energies into the flight config's
energy_cost hints, from the calibration files rather than by hand.

    tools/update_energy_hints.py            # prints old -> new, writes
    tools/update_energy_hints.py --dry-run

Sources: analysis/stage4.5-models/stage_energy.json (analysis/energy_cal_summary.py,
per-run idle floors, I/O term removed) and encoder_energy.json (--enc). Each
hint is the energy of ONE execution of the module on a 99-tile frame -- the
work the scheduler commits to when it picks that slot -- with the demosaic +
tile stages' own share carried by slots 1 and 2. Latency hints are untouched
(they were never biased). A provenance line is prepended to each file's
cost block.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FC = os.path.join(REPO, "experiments", "board", "flight-config")
S = os.path.join(REPO, "analysis", "stage4.5-models")


def main():
    dry = "--dry-run" in sys.argv
    E = json.load(open(os.path.join(S, "stage_energy.json")))
    # Encoder hints come from the MATCHED frame (clear water -- the content the
    # encoder sees in flight and in retention-matched experiments): J2K is
    # 13-17% cheaper there than on a random frame, since rate allocation
    # converges faster on smooth content. Falls back to the random-frame set.
    mp = os.path.join(S, "encoder_energy_matched.json")
    ENC = json.load(open(mp if os.path.exists(mp) else os.path.join(S, "encoder_energy.json")))
    enc_src = "matched frame" if os.path.exists(mp) else "random frame"
    reps = E["_meta"]["settled_reps"]
    # slot -> (uWh, source line)
    # demosaic / tile latencies: medians over the one-pass pipeline runs on
    # the matched frame (2026-09-17); they also become the latency hints.
    lat = {"libdemosaic": [], "libtile": []}
    for v in json.load(open(os.path.join(S, "demosaic_onepass", "e2e_matched_onepass.json"))).values():
        for m, us in v["execs"]:
            if m in lat:
                lat[m].append(us)
    dem_ms, tile_ms = (sorted(lat[k])[len(lat[k]) // 2] / 1000 for k in ("libdemosaic", "libtile"))
    hints = {
        1: (E["fixed"]["uwh"] * dem_ms / (dem_ms + tile_ms), f"demosaic's share of the fixed stages, split by latency ({dem_ms:.0f}:{tile_ms:.0f} ms)"),
        2: (E["fixed"]["uwh"] * tile_ms / (dem_ms + tile_ms), f"tile's share of the fixed stages, split by latency ({dem_ms:.0f}:{tile_ms:.0f} ms)"),
        3: (E["cls_low"]["uwh"], "classification low, all 99 tiles, no upload"),
        4: (E["cls_med"]["uwh"], "classification medium, all 99 tiles, no upload"),
        5: (E["cls_high"]["uwh"], "classification high, all 99 tiles, no upload"),
        6: (E["seg_low"]["uwh"], "segmentation low, 99 tiles, I/O-matched"),
        19: (E["seg_med"]["uwh"], "segmentation medium, 99 tiles, I/O-matched"),
        7: (E["seg_high"]["uwh"], "segmentation high, 99 tiles, I/O-matched"),
        16: (ENC["L1"]["uwh"], f"J2K L1, 99 tiles, n=20, bracketed floor, {enc_src}"),
        17: (ENC["L2"]["uwh"], f"J2K L2, 99 tiles, n=20, bracketed floor, {enc_src}"),
        18: (ENC["L3"]["uwh"], f"J2K L3, 99 tiles, n=20, bracketed floor, {enc_src}"),
        11: (ENC["JXL_e3"]["uwh"], f"JXL e3, 99 tiles, n=20, bracketed floor, {enc_src}"),
        12: (ENC["JXL_e5"]["uwh"], f"JXL e5, 99 tiles, n=20, bracketed floor, {enc_src}"),
    }
    for slot, (uwh, why) in hints.items():
        p = os.path.join(FC, f"module_{slot}.yaml")
        s = open(p).read()
        m = re.search(r"^energy_cost: (\d+)\s*$", s, re.M)
        old = int(m.group(1)); new = int(round(uwh))
        print(f"module_{slot:2}: energy_cost {old:5} -> {new:5}   ({why})")
        if dry:
            continue
        prov = (f"# energy_cost MEASURED 2026-09-17 (tools/update_energy_hints.py): {why};\n"
                f"# idle floor read before every run, settled reps {reps}, I/O term removed\n"
                f"# (analysis/stage4.5-models/stage_energy.json, encoder_energy.json).\n")
        s = re.sub(r"# energy_cost MEASURED[^\n]*\n# idle floor[^\n]*\n# \(analysis[^\n]*\n", "", s)  # no stacking
        s = re.sub(r"^energy_cost: \d+\s*$", prov + f"energy_cost: {new}", s, count=1, flags=re.M)
        if slot in (1, 2):                       # latency hints for the fixed stages, same source
            ms = dem_ms if slot == 1 else tile_ms
            m2 = re.search(r"^latency_cost: (\d+)\s*$", s, re.M)
            print(f"           latency_cost {int(m2.group(1))//1000:5} -> {ms:5.0f} ms")
            s = re.sub(r"^latency_cost: \d+\s*$", f"latency_cost: {int(round(ms * 1000))}", s, count=1, flags=re.M)
        open(p, "w").write(s)


if __name__ == "__main__":
    sys.exit(main())
