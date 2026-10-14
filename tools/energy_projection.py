#!/usr/bin/env python3
"""Project the battery over a multi-day trace on the host: does energy ever
bind, and after how long?

    tools/energy_projection.py --trace d10.trace --platform experiments/platforms/disco2-eol-always.env \\
        --profile-dir analysis/power-profile --service-s 6.64 --energy-mwh 2.85 --soc0 steady

A coarse twin of the board run, for choosing which scenarios to run for days
on the board -- not a result. Frames arrive at their trace times (t_s + all
ff_s so far); one server works FIFO, a frame taking --service-s and debiting
--energy-mwh (the policy's measured mean per frame: P x latency, as the
daemon debits); the board is powered as the platform's BOARD_MODE says
(burst: while it has work, plus the hold); nothing runs while the battery is
at or below the margin (DIPP_BATT_SURVIVAL_ORBITS x bus x orbit, or
DIPP_BATT_MARGIN_WH). Integration as src/battery_simulator.c: forward Euler,
1 s steps, clamped to [0, capacity].

Reports the first time the battery reaches the margin ("binding"), the time
halted, the backlog at the end, the battery minimum, and per orbit the lowest
state of charge (the depth of discharge the battery cycles through).
"""
import argparse, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_arrival_trace as mat  # noqa: E402


def arrivals(path):
    out, ff = [], 0.0
    phase0 = None
    for ln in open(path):
        if ln.startswith("# phase0="):
            phase0 = float(ln.split("=", 1)[1])
        if ln.startswith("#") or not ln.strip():
            continue
        t, f = map(float, ln.split()[:2])
        ff += f
        out.append(t + ff)
    if phase0 is None:
        raise SystemExit(f"{path}: no '# phase0=' header")
    return out, phase0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trace", required=True)
    ap.add_argument("--platform", required=True)
    ap.add_argument("--profile-dir", required=True)
    ap.add_argument("--service-s", type=float, required=True)
    ap.add_argument("--energy-mwh", type=float, required=True)
    ap.add_argument("--soc0", required=True, help="a fraction, or 'steady' (platform-only steady state at phase0)")
    ap.add_argument("--label", default="")
    a = ap.parse_args()

    env = mat.read_env(a.platform)
    prof = mat.read_profile(os.path.join(a.profile_dir, env["DIPP_BATT_PROFILE"]))
    cap = float(env["DIPP_BATT_CAPACITY_WH"]); bus = float(env["DIPP_BATT_BUS_W"])
    T = float(env["DIPP_BATT_ORBIT_MIN"]) * 60
    mode = env["DIPP_BATT_BOARD_MODE"]
    idle = float(env["DIPP_BATT_BOARD_IDLE_W"]) if mode != "off" else 0.0
    hold = float(env["DIPP_BATT_BOARD_HOLD_S"]) if mode == "burst" else 0.0
    if "DIPP_BATT_MARGIN_WH" in env:
        margin = float(env["DIPP_BATT_MARGIN_WH"])
    else:
        margin = float(env["DIPP_BATT_SURVIVAL_ORBITS"]) * bus * T / 3600
    arr, phase0 = arrivals(a.trace)
    soc0 = mat.steady_soc(env, prof, phase0) if a.soc0 == "steady" else float(a.soc0)

    # one orbit of p_gen at 1 s, reused
    n_orb = int(round(T))
    gen = [mat.p_gen(prof, phase0 + k / T) for k in range(n_orb)]
    level = cap * soc0
    t_end = arr[-1] + 3 * T
    i = 0; queue = 0; busy_left = 0.0; last_active = -1e9
    bind_t = None; halted = 0; lo = level; orbit_min = []; omin = level
    board_wh = 0.0; payload_wh = 0.0
    for t in range(int(t_end)):
        while i < len(arr) and arr[i] <= t:
            queue += 1; i += 1
        can_run = level > margin
        if busy_left <= 0 and queue and can_run:
            queue -= 1; busy_left = a.service_s
            e = a.energy_mwh / 1000.0
            level -= e; payload_wh += e
        if busy_left > 0:
            busy_left -= 1; last_active = t
        elif queue and not can_run:
            halted += 1
            if bind_t is None:
                bind_t = t
        on = mode == "always" or (mode == "burst" and (busy_left > 0 or t - last_active < hold))
        b = idle if on else 0.0
        board_wh += b / 3600
        level = min(cap, max(0.0, level + (gen[t % n_orb] - bus - b) / 3600))
        lo = min(lo, level); omin = min(omin, level)
        if (t + 1) % n_orb == 0:
            orbit_min.append(omin); omin = level
    days = t_end / 86400
    dod = [1 - m / cap for m in orbit_min]
    print(f"{a.label or os.path.basename(a.platform):28} margin {margin:5.1f} Wh soc0 {soc0:.3f}: "
          + (f"BINDS after {bind_t / 3600:6.1f} h ({bind_t / T:5.1f} orbits)" if bind_t is not None else "never binds    ")
          + f"; halted {halted / 3600:5.1f} h; min {lo:5.1f} Wh; DoD/orbit max {100 * max(dod):4.1f} % "
          f"median {100 * sorted(dod)[len(dod) // 2]:4.1f} %; board {board_wh / days:5.1f} Wh/d, "
          f"payload {payload_wh / days:4.2f} Wh/d; backlog end {queue}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
