#!/usr/bin/env python3
"""Sentinel-2 acquisition windows over an area of interest, from ESA's
published acquisition plans (KML).

    tools/s2_acquisition.py --kml ~/dipp-datasets/s2-acq/*.kml \\
        --aoi analysis/s2-workload/aoi_greenland.geojson \\
        --merge-gap-s 600 -o analysis/s2-workload/greenland

What is borrowed from Sentinel-2 is the TIMING of a real mission's
acquisitions -- when a sun-synchronous Earth-observation satellite actually
images a region, and for how long -- not its sensor (DISCO-2 has its own
frame geometry; tools/make_arrival_trace.py places DISCO frames on these
windows).

Per Nominal datatake (Mode = NOBS; ESA distributes only those): the swath
polygon is a closed ring of the strip's two long edges; its centre line is
the midpoint of the edges taken at equal along-track fraction, and time runs
linearly from ObservationTimeStart to ObservationTimeStop along it (the scene
spacing, duration / Scenes, is checked to be 3.60-3.62 s). The centre line is
sampled every --step-s seconds and the intervals that lie inside the AOI are
the datatake's WINDOWS over it. Windows from all satellites closer than
--merge-gap-s are merged into BURSTS -- what the payload sees as one imaging
session.

Direction: daytime Sentinel-2 passes are descending, so a datatake starts at
its northern end. Near the pole a pass can cross its northernmost point
inside a datatake; those are counted and reported, never silently flipped.

Outputs (in -o):
  windows.json            every window: satellite, datatake, orbit, start/end
                          UTC, and its centre-line samples (t, lat, lon)
  characterisation.json   per full day covered by every input plan: windows,
                          seconds over the AOI, duty cycle, bursts, burst
                          lengths and gaps (median / IQR), per satellite; and
                          the input files' sha256 -- the W9 table
"""
import argparse, bisect, datetime as dt, hashlib, json, math, os, re, statistics, sys
import xml.etree.ElementTree as ET

NS = {"k": "http://www.opengis.net/kml/2.2"}
UTC = dt.timezone.utc


def parse_time(s):
    return dt.datetime.fromisoformat(s.strip()).replace(tzinfo=UTC)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------ geometry
def km_between(a, b):
    """Equirectangular distance in km between (lat, lon) points; fine at the
    tens-of-km scale used here."""
    lat0 = math.radians((a[0] + b[0]) / 2)
    dx = math.radians(b[1] - a[1]) * math.cos(lat0) * 6371.0
    dy = math.radians(b[0] - a[0]) * 6371.0
    return math.hypot(dx, dy)


def along(points):
    """Cumulative along-edge fraction for a polyline of (lat, lon)."""
    d = [0.0]
    for p, q in zip(points, points[1:]):
        d.append(d[-1] + km_between(p, q))
    return [x / d[-1] for x in d] if d[-1] > 0 else None


def at_fraction(points, frac, f):
    i = max(1, bisect.bisect_left(frac, f))
    i = min(i, len(points) - 1)
    f0, f1 = frac[i - 1], frac[i]
    t = 0.0 if f1 == f0 else (f - f0) / (f1 - f0)
    return (points[i - 1][0] + t * (points[i][0] - points[i - 1][0]),
            points[i - 1][1] + t * (points[i][1] - points[i - 1][1]))


def point_in_ring(lat, lon, ring):
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            x = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x:
                inside = not inside
    return inside


def load_aoi(path):
    d = json.load(open(path))
    polys = []
    for f in d["features"]:
        g = f["geometry"]
        parts = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in parts:
            outer = poly[0]
            lats = [p[1] for p in outer]; lons = [p[0] for p in outer]
            polys.append((outer, poly[1:], (min(lats), max(lats), min(lons), max(lons))))
    if not polys:
        raise SystemExit(f"{path}: no polygons")
    return polys


def in_aoi(lat, lon, polys):
    for outer, holes, (la0, la1, lo0, lo1) in polys:
        if la0 <= lat <= la1 and lo0 <= lon <= lo1 and point_in_ring(lat, lon, outer):
            if not any(point_in_ring(lat, lon, h) for h in holes):
                return True
    return False


# ------------------------------------------------------------------ KML
def datatakes(path):
    sat = re.search(r"(s2[abc])_mp_acq", os.path.basename(path), re.I)
    if not sat:
        raise SystemExit(f"{path}: cannot tell the satellite from the file name")
    sat = sat.group(1).upper()
    root = ET.parse(path).getroot()
    name = root.findtext(".//k:Document/k:name", namespaces=NS) or ""
    m = re.search(r"from (\d{8}T\d{6}) to (\d{8}T\d{6})", name)
    if not m:
        raise SystemExit(f"{path}: no coverage period in the document name: {name!r}")
    cover = tuple(dt.datetime.strptime(x, "%Y%m%dT%H%M%S").replace(tzinfo=UTC) for x in m.groups())
    out = []
    for pm in root.findall(".//k:Placemark", NS):
        d = {e.get("name"): (e.findtext("k:value", namespaces=NS) or "").strip()
             for e in pm.findall(".//k:Data", NS)}
        if d.get("Mode") != "NOBS":
            continue
        coords = pm.findtext(".//k:coordinates", namespaces=NS)
        ring = [tuple(map(float, c.split(",")[:2])) for c in coords.split()]
        out.append({"satellite": sat, "id": d["ID"], "orbit": int(d.get("OrbitAbsolute", -1)),
                    "start": parse_time(d["ObservationTimeStart"]), "stop": parse_time(d["ObservationTimeStop"]),
                    "scenes": int(d["Scenes"]), "ring": [(lat, lon) for lon, lat in ring]})
    return sat, cover, out


def centre_line(take):
    """The two long edges of the strip, start (northern) end first.

    ESA's ring is 2h points (+ the closing repeat): point 0 is the MIDPOINT of
    the strip's start edge, point h the midpoint of its end edge, points
    1..h-1 one long edge and h+1..2h-1 the other, run back. Both midpoints are
    checked against the corners they lie between, so a ring of any other shape
    is reported, not mis-read."""
    ring = take["ring"][:-1] if take["ring"][0] == take["ring"][-1] else take["ring"]
    if len(ring) < 6 or len(ring) % 2:
        return None, "unexpected ring shape"
    h = len(ring) // 2
    for mid, p, q in ((ring[0], ring[1], ring[-1]), (ring[h], ring[h - 1], ring[h + 1])):
        m = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        if km_between(mid, m) > 0.05 * km_between(p, q) + 1.0:
            return None, "unexpected ring shape"
    a = [ring[0]] + ring[1:h] + [ring[h]]          # centre-start, edge, centre-end
    b = [ring[0]] + ring[h + 1:][::-1] + [ring[h]]
    if a[0][0] < a[-1][0]:                          # run north -> south (descending)
        a, b = a[::-1], b[::-1]
    fa, fb = along(a), along(b)
    if fa is None or fb is None:
        return None, "degenerate edge"
    lats = [x[0] for x in a[1:-1]]
    monotone = all(x >= y for x, y in zip(lats, lats[1:]))
    return (a, fa, b, fb), (None if monotone else "apex inside the datatake")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kml", nargs="+", required=True)
    ap.add_argument("--aoi", required=True)
    ap.add_argument("--merge-gap-s", type=float, required=True)
    ap.add_argument("--step-s", type=float, default=1.0,
                    help="centre-line sampling step (resolution of window edges)")
    ap.add_argument("-o", "--outdir", required=True)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    polys = load_aoi(a.aoi)

    takes, covers, sources, notes = [], {}, [], {"apex inside the datatake": 0, "unexpected ring shape": 0}
    for path in a.kml:
        sat, cover, t = datatakes(path)
        covers.setdefault(sat, []).append(cover)
        takes += t
        sources.append({"file": os.path.basename(path), "sha256": sha256(path), "satellite": sat,
                        "from": cover[0].isoformat(), "to": cover[1].isoformat(), "nominal_datatakes": len(t)})

    windows, bad_spacing = [], 0
    for tk in takes:
        dur = (tk["stop"] - tk["start"]).total_seconds()
        if tk["scenes"] and not 3.60 <= dur / tk["scenes"] <= 3.62:
            bad_spacing += 1
        cl, note = centre_line(tk)
        if cl is None:
            notes[note] = notes.get(note, 0) + 1
            continue
        if note:
            notes[note] += 1
        ea, fa, eb, fb = cl
        cur = None
        n = int(dur / a.step_s) + 1
        for i in range(n + 1):
            t = min(i * a.step_s, dur)
            f = t / dur if dur else 0.0
            pa, pb = at_fraction(ea, fa, f), at_fraction(eb, fb, f)
            lat, lon = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
            if in_aoi(lat, lon, polys):
                if cur is None:
                    cur = {"satellite": tk["satellite"], "datatake": tk["id"], "orbit": tk["orbit"],
                           "t0": tk["start"] + dt.timedelta(seconds=t), "samples": []}
                cur["samples"].append([round(t, 3), round(lat, 5), round(lon, 5)])
            elif cur is not None:
                cur["t1"] = tk["start"] + dt.timedelta(seconds=cur["samples"][-1][0])
                windows.append(cur); cur = None
        if cur is not None:
            cur["t1"] = tk["start"] + dt.timedelta(seconds=cur["samples"][-1][0])
            windows.append(cur)
    windows.sort(key=lambda w: w["t0"])

    # Full days covered by every satellite's plans.
    lo = max(min(c[0] for c in cs) for cs in covers.values())
    hi = min(max(c[1] for c in cs) for cs in covers.values())
    day0 = (lo + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0) \
        if lo.time() != dt.time(0) else lo
    days = []
    d = day0
    while d + dt.timedelta(days=1) <= hi:
        days.append(d); d += dt.timedelta(days=1)

    # Bursts: windows (any satellite) closer than merge_gap_s.
    bursts = []
    for w in windows:
        if bursts and (w["t0"] - bursts[-1]["t1"]).total_seconds() < a.merge_gap_s:
            bursts[-1]["t1"] = max(bursts[-1]["t1"], w["t1"]); bursts[-1]["windows"] += 1
        else:
            bursts.append({"t0": w["t0"], "t1": w["t1"], "windows": 1})

    def q(v):
        v = sorted(v)
        if not v:
            return None
        return {"median": round(statistics.median(v), 3),
                "iqr": [round(v[int(0.25 * (len(v) - 1))], 3), round(v[int(0.75 * (len(v) - 1))], 3)],
                "min": round(v[0], 3), "max": round(v[-1], 3), "n": len(v)}

    per_day = []
    for d in days:
        e = d + dt.timedelta(days=1)
        ws = [w for w in windows if d <= w["t0"] < e]
        bs = [b for b in bursts if d <= b["t0"] < e]
        secs = sum((w["t1"] - w["t0"]).total_seconds() for w in ws)
        per_day.append({"day": d.date().isoformat(), "windows": len(ws), "aoi_seconds": round(secs, 1),
                        "duty_cycle": round(secs / 86400, 5), "bursts": len(bs),
                        "by_satellite": {s: sum(1 for w in ws if w["satellite"] == s) for s in sorted(covers)}})
    in_days = [b for b in bursts if days and days[0] <= b["t0"] < days[-1] + dt.timedelta(days=1)]
    gaps = [(n["t0"] - p["t1"]).total_seconds() for p, n in zip(in_days, in_days[1:])]
    char = {
        "aoi": a.aoi, "merge_gap_s": a.merge_gap_s, "step_s": a.step_s, "sources": sources,
        "full_days": [x.date().isoformat() for x in days],
        "datatakes_nominal": len(takes), "scene_spacing_outside_3.60_3.62_s": bad_spacing,
        "centre_line_notes": notes, "windows_total": len(windows),
        "per_day": per_day,
        "summary": {
            "windows_per_day": q([p["windows"] for p in per_day]),
            "aoi_seconds_per_day": q([p["aoi_seconds"] for p in per_day]),
            "duty_cycle_pct": q([100 * p["duty_cycle"] for p in per_day]),
            "bursts_per_day": q([p["bursts"] for p in per_day]),
            "burst_seconds": q([(b["t1"] - b["t0"]).total_seconds() for b in in_days]),
            "inter_burst_gap_s": q(gaps),
        },
    }
    json.dump(char, open(os.path.join(a.outdir, "characterisation.json"), "w"), indent=1)
    json.dump([dict(w, t0=w["t0"].isoformat(), t1=w["t1"].isoformat()) for w in windows],
              open(os.path.join(a.outdir, "windows.json"), "w"))
    s = char["summary"]
    print(f"{len(takes)} nominal datatakes, {len(windows)} windows over {os.path.basename(a.aoi)}; "
          f"{len(days)} full days ({char['full_days'][0]}..{char['full_days'][-1]})")
    print(f"  per day: {s['windows_per_day']['median']} windows, {s['aoi_seconds_per_day']['median']} s over the AOI "
          f"(duty {s['duty_cycle_pct']['median']} %), {s['bursts_per_day']['median']} bursts")
    print(f"  burst length median {s['burst_seconds']['median']} s (IQR {s['burst_seconds']['iqr']}), "
          f"gap median {s['inter_burst_gap_s']['median']} s (IQR {s['inter_burst_gap_s']['iqr']})")
    print(f"  scene spacing outside 3.60-3.62 s: {bad_spacing}; centre-line notes: {notes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
