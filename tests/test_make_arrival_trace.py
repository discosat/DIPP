#!/usr/bin/env python3
"""Host tests for tools/make_arrival_trace.py.   python3 tests/test_make_arrival_trace.py"""
import json, math, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_arrival_trace as m  # noqa: E402

ENV = os.path.join(ROOT, "experiments/platforms/disco2-bol.env")
PDIR = os.path.join(ROOT, "analysis/power-profile")
fails = 0


def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    fails += 0 if ok else 1


env = m.read_env(ENV)
prof = m.read_profile(os.path.join(PDIR, env["DIPP_BATT_PROFILE"]))
T = float(env["DIPP_BATT_ORBIT_MIN"]) * 60

# Orbit
incl, alt = m.sso_inclination(T)
check("SSO inclination for the DISCO-II period is 97-98 deg", 97.0 < incl < 98.0, incl)
check("altitude 500-580 km", 500 < alt < 580, alt)
c, frac = m.eclipse_centre(prof)
check("profile phase 0 is eclipse exit: dark arc ends at 1.0", abs(((c + frac / 2) + 0.5) % 1.0 - 0.5) < 0.003, (c, frac))
check("eclipse 35-36.5 min", 35 * 60 < frac * T < 36.5 * 60, frac * T)

# Steady state: periodic, at most full, full somewhere in the sunlit arc
s_exit = m.steady_soc(env, prof, 0.0)
s_entry = m.steady_soc(env, prof, c - frac / 2)
check("steady SoC at eclipse entry is full", s_entry > 0.999, s_entry)
cap, bus = float(env["DIPP_BATT_CAPACITY_WH"]), float(env["DIPP_BATT_BUS_W"])
expect = 1 - bus * frac * T / 3600 / cap
check("steady SoC at eclipse exit = full minus the eclipse's bus energy", abs(s_exit - expect) < 0.002,
      (s_exit, expect))

# Footprint
fp = m.Footprint(60.0, 10.0, (0.0, -1.0), 46.73, 56.0)
check("footprint contains its centre", fp.contains(60.0, 10.0))
check("along-track half 23.4 km in, 24 km out",
      fp.contains(60.0 + 23.0 / 111.19, 10.0) and not fp.contains(60.0 + 24.0 / 111.19, 10.0))
kmlon = 111.19 * math.cos(math.radians(60))
check("across-track half 28 km in, 29 km out",
      fp.contains(60.0, 10.0 + 27.5 / kmlon) and not fp.contains(60.0, 10.0 + 29.0 / kmlon))

tg = m.Targets(); tg.polys.append(([[9.9, 59.9], [10.1, 59.9], [10.1, 60.1], [9.9, 60.1], [9.9, 59.9]], "box"))
check("polygon target under the frame is hit", tg.hit(fp) == "box")
check("polygon target 1 deg away is not hit", tg.hit(m.Footprint(61.5, 10.0, (0, -1), 46.73, 56)) is None)
big = m.Targets(); big.polys.append(([[9, 59], [11, 59], [11, 61], [9, 61], [9, 59]], "big"))
check("a target larger than the frame (no vertex inside) is hit", big.hit(fp) == "big")


# End to end: Poisson and S2 traces through the CLI and feed_trace --check
def run(args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "tools/make_arrival_trace.py")] + args,
                          capture_output=True, text=True)


common = ["--platform", ENV, "--profile-dir", PDIR, "--realtime-max-gap-s", "60",
          "--slo-urgent", "20", "--slo-routine", "300", "--no-feasibility-check"]
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "p.trace")
    r = run(["poisson", "--mean-s", "100", "--n", "200", "--urgent-fraction", "0.25", "--phase0", "0.3",
             "--seed", "7"] + common + ["-o", p])
    check("poisson trace written", r.returncode == 0, r.stderr)
    rows = [l.split() for l in open(p) if not l.startswith("#")]
    check("200 frames", len(rows) == 200)
    u = sum(1 for x in rows if x[5] == "urgent")
    check("urgent fraction near 0.25", 30 <= u <= 70, u)
    check("urgent rows carry the urgent SLO", all((x[4] == "20") == (x[5] == "urgent") for x in rows))
    big_gaps = [float(x[1]) for x in rows if float(x[1]) > 0]
    check("every gap > 60 s is fast-forwarded in full, none <= 60 s", all(g > 60 for g in big_gaps) and big_gaps)
    t = [float(x[0]) for x in rows]
    check("compressed clock non-decreasing", all(a <= b for a, b in zip(t, t[1:])))
    hdr = open(p).read()
    check("header carries phase0 and soc0_steady", "phase0=0.3000" in hdr and "soc0_steady=" in hdr)
    fc = subprocess.run(["sh", os.path.join(ROOT, "tests/board/feed_trace.sh"), "--check", "--trace", p],
                        capture_output=True, text=True)
    check("feed_trace.sh --check accepts it", fc.returncode == 0, fc.stderr)

    r2 = run(["poisson", "--mean-s", "100", "--n", "200", "--urgent-fraction", "0.25", "--phase0", "0.3",
              "--seed", "7"] + common + ["-o", p + "2"])
    body = lambda f: [l for l in open(f) if not l.startswith(("# generated", "# argv"))]
    check("same seed, same trace (bar the timestamp and argv lines)", body(p) == body(p + "2"))

    r = run(["poisson", "--mean-s", "3", "--n", "5", "--urgent-fraction", "0.1", "--phase0", "0.3", "--seed", "1",
             "--platform", ENV, "--profile-dir", PDIR, "--realtime-max-gap-s", "60", "--slo-routine", "300",
             "--no-feasibility-check", "-o", p])
    check("missing --slo-urgent refused", r.returncode != 0)

    # Feasibility: a cost model where pipeline 2's slowest path is 30 s refuses a 20 s SLO.
    man = os.path.join(ROOT, "experiments/board/flight-config/manifest.yaml")
    cm = {"slots": {str(s): {"n_ref": 1, "points": [], "model": {"lat_ref_us": 5e6, "lat_slope_us": 0},
                             "fit": {}} for s in range(1, 20)}}
    cmp_ = os.path.join(d, "cm.json"); json.dump(cm, open(cmp_, "w"))
    r = run(["poisson", "--mean-s", "3", "--n", "5", "--urgent-fraction", "0.1", "--phase0", "0.3", "--seed", "1",
             "--platform", ENV, "--profile-dir", PDIR, "--realtime-max-gap-s", "60", "--slo-urgent", "20",
             "--slo-routine", "300", "--cost-model", cmp_, "--manifest", man, "--pipelines", "2", "-o", p])
    check("urgent SLO below the EDF bound refused (5 stages x 5 s + 5 s = 30 s > 20 s)",
          r.returncode != 0 and "30.00 s" in (r.stderr + r.stdout), r.stderr)

    # S2 mode on the real windows, if present
    wf = os.path.join(ROOT, "analysis/s2-workload/east_greenland/windows.json")
    if os.path.isfile(wf):
        r = run(["s2", "--windows", wf, "--satellite", "S2A", "--merge-gap-s", "600", "--day", "2026-10-04", "--days", "1",
                 "--first-burst", "0", "--n-bursts", "2", "--targets",
                 os.path.join(ROOT, "analysis/s2-workload/targets.geojson"), "--seed", "1",
                 "--frame-period-s", "6.68", "--frame-along-km", "46.73", "--frame-across-km", "56"]
                + common + ["-o", p])
        check("s2 trace written", r.returncode == 0, r.stderr)
        hdr = open(p).read()
        err = int(hdr.split("phase_vs_latitude_err_s max ")[1].split()[0])
        check("every frame's phase within 30 s of its latitude's", err <= 30, err)
        rows = [l.split() for l in open(p) if not l.startswith("#")]
        intra = [float(b[0]) - float(a[0]) for a, b in zip(rows, rows[1:]) if float(b[1]) == 0]
        check("frames inside a window are 6.68 s apart", any(abs(x - 6.68) < 0.01 for x in intra))

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
