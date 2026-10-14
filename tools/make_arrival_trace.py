#!/usr/bin/env python3
"""Arrival traces for tests/board/feed_trace.sh, from Sentinel-2 acquisition
windows (tools/s2_acquisition.py) or a Poisson process.

    # the main scenario: one satellite over global land, two bursts of one day
    tools/make_arrival_trace.py s2 --windows analysis/s2-workload/global_land/windows.json \\
        --satellite S2A --merge-gap-s 600 --day 2026-10-04 --first-burst 0 --n-bursts 2 \\
        --synthetic-targets 40 --target-aoi analysis/s2-workload/aoi_global_land.geojson --seed 1 \\
        --platform experiments/platforms/disco2-bol.env --profile-dir analysis/power-profile \\
        --frame-period-s 6.68 --frame-along-km 46.73 --frame-across-km 56 \\
        --realtime-max-gap-s 60 --slo-urgent 15 --slo-routine 300 \\
        --cost-model analysis/cost_model.json --pipelines 1,2,3,4,6 -o traces/global-s2a-d4-b0.trace

    # stress: Poisson arrivals
    tools/make_arrival_trace.py poisson --mean-s 3 --n 100 --urgent-fraction 0.1 --seed 1 \\
        --phase0 0.2 --platform ... --profile-dir ... --realtime-max-gap-s 60 \\
        --slo-urgent 15 --slo-routine 300 --cost-model ... --pipelines ... -o traces/poisson3.trace

Nothing has a default (the no-fallbacks rule): every input is on the command
line and is echoed into the trace header.

FRAMES. What is borrowed from Sentinel-2 is when a sun-synchronous imager is
over the region, not its sensor: along each window's centre line a DISCO frame
is taken every --frame-period-s (the 56 km swath across the frame's long edge,
46.73 km along track at ~7 km/s: one frame per 6.68 s, no overlap). A window
shorter than one period still gives one frame.

BURSTS AND THE ORBIT. Windows closer than --merge-gap-s form a burst. The
battery simulator runs DISCO-II's own orbit (period from the platform file),
not Sentinel-2's, so each burst is placed on DISCO's orbit by its latitude:
  * the orbit is taken as beta = 0 (the report's eclipse length matches beta =
    0 geometry, analysis/power-profile/README.md), noon/midnight: the eclipse
    is centred on the ascending node, so the argument of latitude u = 0 at the
    middle of the profile's dark arc (phase 0 of the profile is eclipse exit);
  * the inclination is the sun-synchronous one for that period (J2), derived
    here, not an input;
  * Sentinel-2 images on descending passes, so a frame at latitude phi sits at
    u = 180 deg - asin(sin phi / sin i).
The first burst's first frame fixes phase0 (written to the header; pass it to
run.sh). Every later gap between bursts is the real Sentinel-2 gap, adjusted
by less than half an orbit so that the next burst starts at the phase its
latitude implies; the adjustments are summed in the header. Inside a burst the
real spacing is kept. (With --satellite all, a burst can mix satellites at
different phases; only its first frame is placed, and that is stated.)

GAPS. A gap longer than --realtime-max-gap-s is fast-forwarded in full
(ff_s = gap); feed_trace.sh only skips what the backlog has not used, so
processing time still counts against the gap. Shorter gaps run in real time.

URGENCY. A frame is urgent (--slo-urgent) when its footprint contains a
target, routine (--slo-routine) otherwise. Targets are either a GeoJSON file
(points or polygons: analysis/s2-workload/targets.geojson) or synthetic: a
seeded uniform scatter over the sphere at --synthetic-targets per million
km^2, kept where it falls inside --target-aoi. For a 46.73 x 56 km footprint
the expected urgent fraction is density x 2617 km^2 (40 / Mkm^2 -> ~10 %).

START CHARGE. The header's soc0_steady is the platform-only periodic steady
state at phase0 (bus load, plus the idle board when the platform's
BOARD_MODE is always -- off between bursts otherwise; the profile; integrated exactly as
src/battery_simulator.c does, clamped at full) -- what the battery holds when
a burst begins after a quiet stretch.

FEASIBILITY. With --cost-model, the urgent SLO is checked against the worst
urgent latency the scheduler can give under non-preemptive EDF: the longest
single stage execution that may already be running (blocking) plus the
urgent frame's own path, both at the pipeline's slowest operating point with
all 99 tiles kept, for each of --pipelines (a run uses one pipeline, so
blocking comes from its own stages). An urgent SLO below it is refused: it would measure the
scheduler's granularity, not the policy.
"""
import argparse, bisect, functools, json, math, os, random, statistics, sys
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from s2_acquisition import load_aoi, in_aoi  # noqa: E402

import yaml  # noqa: E402

R_EARTH_KM = 6371.0
MU = 398600.4418           # km^3/s^2
J2 = 1.08263e-3
RE_EQ = 6378.137
SSO_RATE = 2 * math.pi / (365.2422 * 86400)   # rad/s, nodal precession of an SSO
DARK_W = 0.5               # src/battery_simulator.c: sunlit iff p_gen > 0.5 W


# ------------------------------------------------------------------ platform
def read_env(path):
    out = {}
    for ln in open(path):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def read_profile(path):
    ph, w = [], []
    for ln in open(path):
        if ln.startswith("#") or not ln.strip():
            continue
        a, b = ln.split()
        ph.append(float(a)); w.append(float(b))
    if len(ph) < 2:
        raise SystemExit(f"{path}: fewer than 2 samples")
    return ph, w


def p_gen(prof, phase):
    """Linear interpolation with wrap -- battery_simulator_p_gen."""
    ph, w = prof
    phase -= math.floor(phase)
    n = len(ph)
    i = bisect.bisect_right(ph, phase)        # first sample > phase, as the C loop finds
    if i == 0:
        p0, w0, p1, w1 = ph[-1] - 1.0, w[-1], ph[0], w[0]
    elif i == n:
        p0, w0, p1, w1 = ph[-1], w[-1], ph[0] + 1.0, w[0]
    else:
        p0, w0, p1, w1 = ph[i - 1], w[i - 1], ph[i], w[i]
    return w0 + (w1 - w0) * (phase - p0) / (p1 - p0)


def eclipse_centre(prof):
    """Middle of the profile's dark arc, as a phase. One contiguous arc is
    required; anything else is refused."""
    dark = [p_gen(prof, (k + 0.5) / 3600) <= DARK_W for k in range(3600)]
    edges = [k for k in range(3600) if dark[k] and not dark[k - 1]]
    if len(edges) != 1:
        raise SystemExit(f"profile has {len(edges)} dark arcs; need exactly one")
    k0 = edges[0]
    n = 0
    while dark[(k0 + n) % 3600]:
        n += 1
    return ((k0 + n / 2) / 3600) % 1.0, n / 3600


def sso_inclination(period_s):
    a = (MU * (period_s / (2 * math.pi)) ** 2) ** (1 / 3)
    c = -SSO_RATE * 2 * a ** 3.5 / (3 * J2 * RE_EQ ** 2 * math.sqrt(MU))
    if not -1 <= c <= 1:
        raise SystemExit(f"no sun-synchronous inclination for a {period_s:.0f} s orbit")
    return math.degrees(math.acos(c)), a - RE_EQ


def steady_soc(env, prof, phase0, orbits=30, step_s=1.0):
    cap = float(env["DIPP_BATT_CAPACITY_WH"])
    bus = float(env["DIPP_BATT_BUS_W"])
    mode = env["DIPP_BATT_BOARD_MODE"]
    if mode == "always":            # the idle board is part of the load before the trace too
        bus += float(env["DIPP_BATT_BOARD_IDLE_W"])
    elif mode not in ("burst", "off"):
        raise SystemExit(f"DIPP_BATT_BOARD_MODE={mode}: not burst | always | off")
    T = float(env["DIPP_BATT_ORBIT_MIN"]) * 60
    level = cap
    n = int(round(T / step_s))
    dwh = [(p_gen(prof, phase0 + k * step_s / T) - bus) * step_s / 3600 for k in range(n)]
    for o in range(orbits):
        for d in dwh:
            level = min(cap, max(0.0, level + d))
    return level / cap


# ------------------------------------------------------------------ geometry
def to_xy(lat0, lon0, lat, lon):
    """Local east/north km of (lat, lon) about (lat0, lon0)."""
    dlon = (lon - lon0 + 540) % 360 - 180
    return (math.radians(dlon) * math.cos(math.radians(lat0)) * R_EARTH_KM,
            math.radians(lat - lat0) * R_EARTH_KM)


def interp(samples, t):
    """Centre-line (lat, lon) at window time t (s since the datatake start)."""
    if t <= samples[0][0]:
        return samples[0][1], samples[0][2]
    for a, b in zip(samples, samples[1:]):
        if a[0] <= t <= b[0]:
            f = 0 if b[0] == a[0] else (t - a[0]) / (b[0] - a[0])
            dlon = (b[2] - a[2] + 540) % 360 - 180
            return a[1] + f * (b[1] - a[1]), (a[2] + f * dlon + 540) % 360 - 180
    return samples[-1][1], samples[-1][2]


def heading(samples, t, fallback_lat):
    """Unit along-track vector (east, north) at t; a one-sample window takes
    the descending sun-synchronous direction (due south, slightly west)."""
    if len(samples) < 2:
        return (0.0, -1.0)
    i = max(1, min(len(samples) - 1, next((k for k, s in enumerate(samples) if s[0] >= t), len(samples) - 1)))
    a, b = samples[i - 1], samples[i]
    e, n = to_xy(a[1], a[2], b[1], b[2])
    m = math.hypot(e, n)
    return (e / m, n / m) if m > 0 else (0.0, -1.0)


class Footprint:
    def __init__(self, lat, lon, hd, along_km, across_km):
        self.lat, self.lon, self.hd = lat, lon, hd
        self.ha, self.hc = along_km / 2, across_km / 2

    def local(self, lat, lon):
        e, n = to_xy(self.lat, self.lon, lat, lon)
        ue, un = self.hd
        return e * ue + n * un, -e * un + n * ue          # along, across

    def contains(self, lat, lon):
        a, c = self.local(lat, lon)
        return abs(a) <= self.ha and abs(c) <= self.hc

    def corners(self):
        ue, un = self.hd
        out = []
        for sa, sc in ((1, 1), (1, -1), (-1, -1), (-1, 1)):
            e = sa * self.ha * ue - sc * self.hc * un
            n = sa * self.ha * un + sc * self.hc * ue
            out.append((self.lat + math.degrees(n / R_EARTH_KM),
                        self.lon + math.degrees(e / (R_EARTH_KM * math.cos(math.radians(self.lat))))))
        return out


# ------------------------------------------------------------------ targets
class Targets:
    def __init__(self):
        self.points = []           # (lat, lon, name)
        self.polys = []            # (ring[(lon, lat)], name)

    def hit(self, fp):
        for lat, lon, name in self.points:
            if abs(lat - fp.lat) < 2 and fp.contains(lat, lon):
                return name
        for ring, name in self.polys:
            if not any(abs(p[1] - fp.lat) < 2 for p in ring):
                continue
            if any(fp.contains(p[1], p[0]) for p in ring):
                return name
            cl = [(p[0], p[1]) for p in ring]
            if any(in_aoi(la, lo, [(cl, [], (-90, 90, -180, 180))]) for la, lo in fp.corners() + [(fp.lat, fp.lon)]):
                return name
        return None


def file_targets(path):
    t = Targets()
    d = json.load(open(path))
    for f in d["features"]:
        g, name = f["geometry"], f.get("properties", {}).get("name", "?").replace(" ", "_")
        if g["type"] == "Point":
            t.points.append((g["coordinates"][1], g["coordinates"][0], name))
        elif g["type"] == "Polygon":
            t.polys.append((g["coordinates"][0], name))
        elif g["type"] == "MultiPolygon":
            for poly in g["coordinates"]:
                t.polys.append((poly[0], name))
        else:
            raise SystemExit(f"{path}: unsupported target geometry {g['type']}")
    if not t.points and not t.polys:
        raise SystemExit(f"{path}: no targets")
    return t


def synthetic_targets(density_per_mkm2, aoi_path, seed, lat_band):
    """Uniform on the sphere, count = density x 510.07 Mkm^2, kept inside the
    AOI and (to save time) inside the latitude band the trace visits. The
    scatter is drawn whole and then filtered, so a target's position does not
    depend on the band."""
    rng = random.Random(seed)
    polys = load_aoi(aoi_path)
    n = int(round(density_per_mkm2 * 510.07))
    t = Targets()
    lo, hi = lat_band
    for k in range(n):
        lat = math.degrees(math.asin(2 * rng.random() - 1))
        lon = 360 * rng.random() - 180
        if lo - 1 <= lat <= hi + 1 and in_aoi(lat, lon, polys):
            t.points.append((lat, lon, f"syn{k}"))
    return t, n


# ------------------------------------------------------------------ feasibility
def feasibility_bound(cost_model, manifest, pipelines):
    """Worst urgent latency under non-preemptive EDF: blocking (the longest
    single execution any of the pipelines can have in flight, at one frame)
    plus the urgent frame's own path at its slowest operating point."""
    cm = json.load(open(cost_model))["slots"]
    man = yaml.safe_load(open(manifest))
    base = os.path.dirname(manifest)

    def lat_at(slot, n):
        e = cm.get(str(slot))
        if e is None:
            raise SystemExit(f"cost model has no slot {slot}; was it swept?")
        pts = {p["n"]: p["lat_us"] for p in e["points"]}
        if n in pts:
            return pts[n] / 1e6
        if e["model"] is None:
            raise SystemExit(f"slot {slot}: n={n} not measured and no line ({e['fit']})")
        m = e["model"]
        return max(0.0, m["lat_ref_us"] + m["lat_slope_us"] * (n - e["n_ref"])) / 1e6

    per = {}
    for p in pipelines:
        doc = yaml.safe_load(open(os.path.join(base, man["pipelines"][p])))
        own, worst = 0.0, 0.0
        for st in doc["pipeline"]:
            slots = [im["param_id"] for im in st["implementations"]]   # param_id = slot
            name = st["name"]
            n = 1 if name in ("libdemosaic", "libtile", "libsingle_module") else 99
            lat = max(lat_at(s, n) for s in slots)
            own += lat
            worst = max(worst, lat)
        per[p] = {"own_s": own, "longest_stage_s": worst, "bound_s": own + worst}
    # A run uses one pipeline, so blocking comes from that pipeline's own
    # stages; the trace must be feasible for every pipeline it will run with.
    worst_p = max(per, key=lambda p: per[p]["bound_s"])
    return per[worst_p]["bound_s"], worst_p, per


# ------------------------------------------------------------------ main
def common_args(ap):
    ap.add_argument("--platform", required=True)
    ap.add_argument("--profile-dir", required=True, help="where the platform's profile file lives on the host")
    ap.add_argument("--realtime-max-gap-s", type=float, required=True)
    ap.add_argument("--slo-urgent", type=int, required=True)
    ap.add_argument("--slo-routine", type=int, required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--cost-model", help="enables the urgent-SLO feasibility check")
    g.add_argument("--no-feasibility-check", action="store_true")
    ap.add_argument("--manifest", help="flight-config manifest.yaml (with --cost-model)")
    ap.add_argument("--pipelines", help="comma list (with --cost-model)")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("-o", "--out", required=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    s2 = sub.add_parser("s2")
    s2.add_argument("--windows", required=True)
    s2.add_argument("--satellite", required=True, help="S2A | S2B | S2C | all")
    s2.add_argument("--merge-gap-s", type=float, required=True)
    s2.add_argument("--day", required=True)
    s2.add_argument("--days", type=int, required=True, help="bursts are counted from --day over this many days")
    s2.add_argument("--first-burst", type=int, required=True)
    s2.add_argument("--n-bursts", type=int, required=True)
    s2.add_argument("--frame-period-s", type=float, required=True)
    s2.add_argument("--frame-along-km", type=float, required=True)
    s2.add_argument("--frame-across-km", type=float, required=True)
    tg = s2.add_mutually_exclusive_group(required=True)
    tg.add_argument("--targets")
    tg.add_argument("--synthetic-targets", type=float, metavar="PER_MKM2")
    s2.add_argument("--target-aoi")
    common_args(s2)
    po = sub.add_parser("poisson")
    po.add_argument("--mean-s", type=float, required=True)
    po.add_argument("--n", type=int, required=True)
    po.add_argument("--urgent-fraction", type=float, required=True)
    po.add_argument("--phase0", type=float, required=True)
    common_args(po)
    a = ap.parse_args()

    if a.slo_urgent <= 0 or a.slo_routine <= 0:
        raise SystemExit("SLOs must be positive")
    if a.cost_model and not (a.manifest and a.pipelines):
        raise SystemExit("--cost-model needs --manifest and --pipelines")

    env = read_env(a.platform)
    for k in ("DIPP_BATT_ORBIT_MIN", "DIPP_BATT_PROFILE", "DIPP_BATT_CAPACITY_WH", "DIPP_BATT_BUS_W",
              "DIPP_BATT_BOARD_MODE"):
        if k not in env:
            raise SystemExit(f"{a.platform}: {k} missing")
    prof_path = os.path.join(a.profile_dir, env["DIPP_BATT_PROFILE"])
    if not os.path.isfile(prof_path):
        raise SystemExit(f"profile not found: {prof_path}")
    prof = read_profile(prof_path)
    T = float(env["DIPP_BATT_ORBIT_MIN"]) * 60
    ecl_c, ecl_frac = eclipse_centre(prof)
    incl, alt = sso_inclination(T)
    rng = random.Random(a.seed)

    header = [f"generated {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} by "
              f"tools/make_arrival_trace.py", "argv " + " ".join(sys.argv[1:])]
    frames = []       # dicts: time (s, true), class, scene, datatake, lat, lon, target
    notes = {}

    if a.mode == "s2":
        if a.synthetic_targets is not None and not a.target_aoi:
            raise SystemExit("--synthetic-targets needs --target-aoi")
        ws = json.load(open(a.windows))
        if a.satellite != "all":
            ws = [w for w in ws if w["satellite"] == a.satellite]
        if not ws:
            raise SystemExit(f"no windows for {a.satellite}")
        for w in ws:
            w["t0d"] = dt.datetime.fromisoformat(w["t0"]); w["t1d"] = dt.datetime.fromisoformat(w["t1"])
            w["s0"] = w["samples"][0][0]
        ws.sort(key=lambda w: w["t0d"])
        bursts = []
        for w in ws:
            if bursts and (w["t0d"] - bursts[-1]["t1"]).total_seconds() < a.merge_gap_s:
                bursts[-1]["w"].append(w); bursts[-1]["t1"] = max(bursts[-1]["t1"], w["t1d"])
            else:
                bursts.append({"t0": w["t0d"], "t1": w["t1d"], "w": [w]})
        day0 = dt.datetime.fromisoformat(a.day).replace(tzinfo=dt.timezone.utc)
        if a.days < 1:
            raise SystemExit("--days must be >= 1")
        day = [b for b in bursts if day0 <= b["t0"] < day0 + dt.timedelta(days=a.days)]
        if a.first_burst + a.n_bursts > len(day):
            raise SystemExit(f"{a.day} + {a.days} d has {len(day)} bursts; asked for "
                             f"{a.first_burst}..{a.first_burst + a.n_bursts - 1}")
        chosen = day[a.first_burst:a.first_burst + a.n_bursts]
        epoch = chosen[0]["t0"]
        for bi, b in enumerate(chosen):
            for w in b["w"]:
                dur = (w["t1d"] - w["t0d"]).total_seconds()
                k = 0
                while True:
                    off = k * a.frame_period_s
                    if k and off > dur:
                        break
                    t_dt = w["s0"] + off
                    lat, lon = interp(w["samples"], t_dt)
                    hd = heading(w["samples"], t_dt, lat)
                    frames.append({"t": (w["t0d"] - epoch).total_seconds() + off, "burst": bi,
                                   "datatake": f"{w['satellite']}:{w['datatake']}",
                                   "lat": lat, "lon": lon, "hd": hd})
                    k += 1
        frames.sort(key=lambda f: f["t"])
        if a.targets:
            targets, tdesc = file_targets(a.targets), f"file {a.targets}"
        else:
            lats = [f["lat"] for f in frames]
            targets, ndraw = synthetic_targets(a.synthetic_targets, a.target_aoi, a.seed, (min(lats), max(lats)))
            tdesc = (f"synthetic {a.synthetic_targets}/Mkm^2 over {a.target_aoi} seed {a.seed} "
                     f"({ndraw} drawn on the sphere, {len(targets.points)} in the AOI and the trace's band)")
        for f in frames:
            fp = Footprint(f["lat"], f["lon"], f["hd"], a.frame_along_km, a.frame_across_km)
            f["target"] = targets.hit(fp)

        # Orbit placement: phase of each burst's first frame from its latitude.
        def phase_of_lat(lat):
            s = math.sin(math.radians(lat)) / math.sin(math.radians(incl))
            s = max(-1.0, min(1.0, s))
            u = 180.0 - math.degrees(math.asin(s))            # descending
            return (ecl_c + u / 360.0) % 1.0
        shift, adj, lat_err = 0.0, [], []
        prev_last = None
        for bi in range(len(chosen)):
            fb = [f for f in frames if f["burst"] == bi]
            want = phase_of_lat(fb[0]["lat"])
            if bi == 0:
                phase0 = want
            else:
                t_first = fb[0]["t"] + shift
                gap = t_first - prev_last
                have = (phase0 + t_first / T) % 1.0
                d = ((want - have + 0.5) % 1.0 - 0.5) * T
                if gap + d < a.frame_period_s:
                    d += T
                shift += d
                adj.append(d)
            for f in fb:
                f["t"] += shift
                ph = (phase0 + f["t"] / T) % 1.0
                lat_err.append(abs(((ph - phase_of_lat(f["lat"])) + 0.5) % 1.0 - 0.5) * T)
            prev_last = fb[-1]["t"]
        notes.update({
            "source": f"S2 windows {a.windows} satellite {a.satellite} day {a.day} (+{a.days} d) bursts "
                      f"{a.first_burst}..{a.first_burst + a.n_bursts - 1} of {len(day)} (merge gap {a.merge_gap_s} s)",
            "epoch_utc": epoch.isoformat(), "targets": tdesc,
            "orbit": f"beta 0 noon/midnight SSO, period {T:.1f} s, altitude {alt:.0f} km, inclination {incl:.2f} deg; "
                     f"eclipse centre at profile phase {ecl_c:.4f} ({ecl_frac * T / 60:.1f} min dark)",
            "inter_burst_adjust_s": " ".join(f"{d:+.0f}" for d in adj) or "none",
            "phase_vs_latitude_err_s": f"max {max(lat_err):.0f} median {statistics.median(lat_err):.0f}",
        })
    else:
        if not 0 <= a.urgent_fraction <= 1:
            raise SystemExit("--urgent-fraction must be in [0, 1]")
        t = 0.0
        for k in range(a.n):
            if k:
                t += rng.expovariate(1.0 / a.mean_s)
            frames.append({"t": t, "burst": 0, "datatake": "poisson",
                           "target": "bernoulli" if rng.random() < a.urgent_fraction else None})
        phase0 = a.phase0 % 1.0
        notes.update({"source": f"Poisson mean {a.mean_s} s n {a.n} urgent fraction {a.urgent_fraction} seed {a.seed}"})

    # Fast-forward split and the compressed clock.
    lines, tc, prev = [], 0.0, None
    ff_total = 0.0
    for i, f in enumerate(frames):
        gap = 0.0 if prev is None else f["t"] - prev
        ff = gap if gap > a.realtime_max_gap_s else 0.0
        tc += gap - ff
        ff_total += ff
        urgent = f["target"] is not None
        f["slo"] = a.slo_urgent if urgent else a.slo_routine
        lines.append(f"{tc:.3f} {ff:.3f} {i + 1} {f['datatake']} {f['slo']} {'urgent' if urgent else 'routine'}")
        prev = f["t"]

    n_urg = sum(1 for f in frames if f["target"] is not None)
    soc0 = steady_soc(env, prof, phase0)
    bound_txt = "not checked (--no-feasibility-check)"
    if a.cost_model:
        pl = [int(x) for x in a.pipelines.split(",")]
        bound, worst_p, per = feasibility_bound(a.cost_model, a.manifest, pl)
        bound_txt = (f"{bound:.2f} s (pipeline {worst_p}: longest stage as blocking + own path at the "
                     f"slowest operating point, 99 tiles kept); per pipeline "
                     + " ".join(f"p{p}:{v['bound_s']:.2f}" for p, v in per.items()))
        if a.slo_urgent < bound:
            raise SystemExit(f"REFUSING: urgent SLO {a.slo_urgent} s is below the worst urgent latency "
                             f"the scheduler can give, {bound:.2f} s ({bound_txt})")
    header += [f"{k} {v}" for k, v in notes.items()]
    header += [
        f"platform {a.platform} profile {prof_path}",
        f"phase0={phase0:.4f}",
        f"soc0_steady={soc0:.4f}",
        f"frames={len(frames)} urgent={n_urg} routine={len(frames) - n_urg} "
        f"urgent_frac={n_urg / len(frames):.3f} bursts={len({f['burst'] for f in frames})}",
        f"span_s={frames[-1]['t'] - frames[0]['t']:.1f} realtime_s={tc:.1f} ff_total_s={ff_total:.1f}",
        f"slo urgent={a.slo_urgent} routine={a.slo_routine}; urgent feasibility bound {bound_txt}",
        "columns: t_s ff_s scene datatake slo_s class (tests/board/feed_trace.sh)",
    ]
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as fh:
        fh.write("".join(f"# {h}\n" for h in header))
        fh.write("\n".join(lines) + "\n")
    print(f"wrote {a.out}")
    for h in header[2:]:
        print("  " + h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
