#!/usr/bin/env python3
"""Do the JPEG 2000 rungs actually separate in energy, or only in time?

    analysis/j2k_repeat_stats.py

The scheduler chooses an effort level by comparing predicted latency and energy
against what it has left, so a rung that is not distinguishable in energy is not
a rung the energy half of that decision can use. A single run per config cannot
answer this: the rungs span ~0.6 mWh end to end, and two configs that do the
SAME work (resync markers on and off) read 0.4-0.6 mWh apart. That is a noise
floor the size of the whole effect.

So each rung is repeated and reported as mean +/- spread, against a noise floor
estimated from the repeats themselves. Energy is idle-corrected,

    E_work = (E_run - P_idle*T_run) - (E_base - P_idle*T_base)

because run lengths differ by tens of seconds and the board floor (~2.5 W) is
worth more over that gap than the module energy being measured. Reporting
E_run - E_base instead would credit a slow encoder with idle seconds it merely
sat through -- for JPEG XL effort 5, an 81.4 s run against a 56.2 s baseline
carries ~25 s of floor, several times the true figure.
"""

import glob
import json
import os
import statistics as st

D = "analysis/stage4.5-models"
TILES = 99
LABEL = {"49.81:1:1:6": "L=1", "99.56:1:2:6": "L=2",
         "149.31:1:3:6": "L=3", "250.36:1:5:6": "L=5"}


def load_all():
    runs, idle, base = {}, None, None
    for f in sorted(glob.glob(os.path.join(D, "dev_j2k_rep*.json")) +
                    [os.path.join(D, "dev_j2k_repeats.json"),
                     os.path.join(D, "dev_j2k_rungs.json")]):
        if not os.path.exists(f):
            continue
        for k, v in json.load(open(f)).items():
            if k == "idle":
                idle = v["energy"].get("avg_power_w", idle)
            elif k == "baseline":
                base = v
            else:
                runs.setdefault(k, []).append(v)
    return runs, idle, base


def main():
    runs, idle, base = load_all()
    if idle is None:
        print("no idle measurement yet; energy withheld")
    print(f"idle floor: {idle:.4f} W" if idle else "")
    if base:
        print(f"baseline  : {base['energy']['energy_wh']*1000:.3f} mWh over "
              f"{base['energy']['measured_s']:.1f} s")
    print()
    print(f"{'rung':8} {'n':>2} {'ms/tile':>18} {'work uWh/frame':>22}")

    rows = []
    for k in [c for c in LABEL if c in runs] or sorted(runs):
        vs = runs[k]
        ms = [sorted(v["classify_us"])[len(v["classify_us"]) // 2] / 1000 / TILES
              for v in vs if v.get("classify_us")]
        uwh = []
        if idle and base:
            eb = base["energy"]["energy_wh"] - idle * base["energy"]["measured_s"] / 3600
            for v in vs:
                e = v["energy"]
                if e.get("energy_wh") is None:
                    continue
                er = e["energy_wh"] - idle * e["measured_s"] / 3600
                uwh.append((er - eb) * 1e6)
        f_ms = (f"{st.mean(ms):7.1f} +/- {(st.stdev(ms) if len(ms) > 1 else 0):4.1f}"
                if ms else "      -")
        f_e = (f"{st.mean(uwh):9.1f} +/- {(st.stdev(uwh) if len(uwh) > 1 else 0):6.1f}"
               if uwh else "        -")
        print(f"{LABEL.get(k, k):8} {len(vs):2} {f_ms:>18} {f_e:>22}")
        rows.append((LABEL.get(k, k), ms, uwh))

    # The noise floor, taken from the repeats rather than assumed: the largest
    # within-rung spread is the smallest between-rung gap that means anything.
    spreads = [st.stdev(u) for _, _, u in rows if len(u) > 1]
    if spreads and len(rows) > 1:
        floor = max(spreads)
        print(f"\nwithin-rung spread (worst): +/- {floor:.1f} uWh")
        means = [(n, st.mean(u)) for n, _, u in rows if u]
        for (n1, m1), (n2, m2) in zip(means, means[1:]):
            gap = m2 - m1
            verdict = "separated" if abs(gap) > 2 * floor else "NOT separated"
            print(f"  {n1} -> {n2}: {gap:+8.1f} uWh   {verdict}")


if __name__ == "__main__":
    main()
