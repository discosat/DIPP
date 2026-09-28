#!/usr/bin/env python3
"""Build a generation profile for the battery simulator (DIPP_BATT_PROFILE).

    tools/make_power_profile.py --digitised analysis/power-profile/fig7-3_digitised.csv \
        --orbit-avg-w 17.3 --conversion 0.95 -o analysis/power-profile/disco2_fig7-3_bol.profile
    tools/make_power_profile.py --digitised analysis/power-profile/fig7-3_digitised.csv \
        --orbit-avg-w 17.3 --degrade 0.92833 --conversion 0.95 \
        -o analysis/power-profile/disco2_fig7-3_eol.profile
    tools/make_power_profile.py --rect --gen-w 27.78 --orbit-min 95.43 --eclipse-min 36 \
        --conversion 0.95 -o rect.profile

Output: "# comment" lines, then "phase p_gen_w". Phase 0 is ECLIPSE EXIT (the
simulator's convention). The values are power DELIVERED TO THE BUS: panel
output times --conversion, the power point tracker's efficiency. Every input
that sets a number is required -- there are no defaults to inherit silently --
and every one is recorded in the file's header.

Digitised: the shape is rescaled to --orbit-avg-w (panel output), which for
DISCO-II is the report's own 17.3 W (§7.2.1) and removes the digitisation
error (17.17 W read); --degrade then scales it (EOL: 27.2/29.3, the report's
5-year cell-efficiency loss); --conversion last (0.95, §5.1.2). 360 samples.

Rectangular: four samples with exact edges (a step, not a ramp between
bins), so its orbit average is exactly gen_w x sunlit fraction x conversion.

battery_simulator.c reads with a 128-byte line buffer and rejects longer
lines, so every line here stays well under that.
"""
import argparse, csv, hashlib, re, sys

import numpy as np

N_BINS = 360


def read_digitised(path):
    header, rows = [], []
    with open(path) as fh:
        for line in fh:
            if line.startswith("#"):
                header.append(line.rstrip("\n"))
            else:
                break
        fh.seek(0)
        body = (l for l in fh if not l.startswith("#"))
        for r in csv.DictReader(body):
            rows.append((float(r["phase"]), float(r["total_w"])))
    m = next((re.search(r"eclipse phase ([0-9.]+)\.\.([0-9.]+)", h) for h in header
              if "eclipse phase" in h), None)
    if not m:
        sys.exit(f"{path}: no '# eclipse phase a..b' header line")
    ph, w = np.array(rows).T
    return ph, w, float(m.group(1)), float(m.group(2))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--digitised", help="CSV from tools/digitise_fig7_3.py")
    src.add_argument("--rect", action="store_true", help="rectangular: GEN_W sunlit, 0 in eclipse")
    ap.add_argument("--conversion", type=float, required=True,
                    help="panel-to-bus efficiency (power point tracker), e.g. 0.95")
    ap.add_argument("--orbit-avg-w", type=float, help="digitised: rescale the shape to this panel orbit average")
    ap.add_argument("--degrade", type=float, default=1.0,
                    help="digitised: multiply panel output by this (EOL); 1.0 = none")
    ap.add_argument("--gen-w", type=float, help="rect: sunlit panel output")
    ap.add_argument("--orbit-min", type=float, help="rect: orbit period")
    ap.add_argument("--eclipse-min", type=float, help="rect: eclipse duration")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    if not 0.0 < a.conversion <= 1.0:
        ap.error("--conversion must be in (0, 1]")

    if a.rect:
        missing = [f for f in ("gen_w", "orbit_min", "eclipse_min") if getattr(a, f) is None]
        if missing:
            ap.error("--rect needs " + ", ".join("--" + m.replace("_", "-") for m in missing))
        sunlit = 1.0 - a.eclipse_min / a.orbit_min
        g = a.gen_w * a.conversion
        eps = 1e-6
        phases = np.array([0.0, sunlit - eps, sunlit, 1.0 - eps])
        w = np.array([g, g, 0.0, 0.0])
        avg = g * sunlit
        note = [f"# rectangular gen_w={a.gen_w} orbit_min={a.orbit_min} eclipse_min={a.eclipse_min}",
                f"# conversion x{a.conversion} (panel -> bus)"]
        scale = a.conversion
    else:
        if a.orbit_avg_w is None:
            ap.error("--digitised needs --orbit-avg-w (the panel orbit average to scale the shape to)")
        phases = np.arange(N_BINS) / N_BINS
        ph, tw, ecl_in, ecl_out = read_digitised(a.digitised)
        # Rotate so phase 0 is eclipse exit, then sample (periodic interpolation).
        src_phase = (phases + ecl_out) % 1.0
        w = np.interp(src_phase, ph, tw, period=1.0)
        raw_avg = w.mean()
        scale = a.orbit_avg_w / raw_avg * a.degrade * a.conversion
        w = w * scale
        avg = w.mean()
        sha = hashlib.sha256(open(a.digitised, "rb").read()).hexdigest()[:16]
        note = [f"# SI-DISCOII-DR-001 Fig 7-3, digitised; csv sha256[:16]={sha}",
                f"# rotated: phase 0 = eclipse exit (csv phase {ecl_out:.4f})",
                f"# shape {raw_avg:.3f} W -> panel {a.orbit_avg_w} W x degrade {a.degrade}",
                f"# x conversion {a.conversion} (panel -> bus) = {avg:.3f} W orbit avg"]

    with open(a.out, "w") as fh:
        for line in note:
            fh.write(line + "\n")
        fh.write(f"# delivered_orbit_avg_w={avg:.3f} peak_w={w.max():.2f}\n")
        fh.write("# phase p_gen_w\n")
        for p, v in zip(phases, w):
            fh.write(f"{p:.6f} {v:.4f}\n")
    longest = max(len(l) for l in open(a.out))
    assert longest < 120, f"line of {longest} bytes would be rejected by the daemon's 128-byte reader"
    print(f"wrote {a.out}: delivered orbit avg {avg:.3f} W, peak {w.max():.2f} W, scale x{scale:.4f}")


if __name__ == "__main__":
    main()
