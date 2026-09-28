#!/usr/bin/env python3
"""Digitise Figure 7-3 of SI-DISCOII-DR-001 (per-panel PV power over one orbit).

    tools/digitise_fig7_3.py --pdf "SI-DISCO-II Satellite Design Report v1.0-1.pdf" \
        -o analysis/power-profile/

The figure is a Grafana screenshot embedded in the PDF at 1691 x 531 px
(page 39, third image). It is extracted at native resolution with `pdfimages`
-- no re-rasterisation -- and each series is read by line colour, column by
column:

    purple (186, 67, 169)  both deployable panels (idx 6)
    red    (222, 76, 76)   y- body panel          (idx 4)
    green  (132, 167, 115) x+ body panel          (idx 0)

(idx 1, yellow, is identically zero.) Where one line is drawn over another
the hidden one has no pixels in that column and is linearly interpolated
across the gap; the eclipse, where every series lies under the yellow zero
line, reads as zero.

Axis calibration comes from the gridlines, found from the image itself:
0 W at y = 431.5 and 20 W at y = 72.5; 15:27:45 at x = 180.5 and 15:28:35 at
x = 1529.5. The plot spans x = 59..1646. The report says the figure is one
orbit, run faster than real time, and the image agrees: every series ends
where it starts (deployables 13.6 W, y- 9.6 W, x+ 0 W), so the window is
taken as exactly one orbit and time is expressed as orbit phase.

Outputs fig7-3_digitised.csv (phase from the window start, per-series and
total W) and fig7-3_overlay.png (the digitised values redrawn over the
source, for a visual check), and prints the checks against the report's own
numbers: 17.3 W orbit average, 27.5 Wh per orbit (Fig 7-4).
"""
import argparse, csv, hashlib, os, shutil, subprocess, sys, tempfile

import numpy as np
from PIL import Image, ImageDraw

PAGE, IMAGE_INDEX = 39, 2
ORBIT_MIN = 1440.0 / 15.09          # mean motion 15.09 rev/day, report §7.2.1
X0, X1 = 59, 1646                    # plot area, px
Y_0W, Y_20W = 431.5, 72.5            # gridlines
SERIES = {
    "deployables_w": (186, 67, 169),
    "y_minus_w": (222, 76, 76),
    "x_plus_w": (132, 167, 115),
}
COLOUR_TOL = 40                      # Euclidean RGB distance


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def extract_png(pdf, workdir):
    if not shutil.which("pdfimages"):
        sys.exit("pdfimages (poppler-utils) not found")
    prefix = os.path.join(workdir, "p")
    subprocess.run(["pdfimages", "-png", "-f", str(PAGE), "-l", str(PAGE), pdf, prefix], check=True)
    path = f"{prefix}-{IMAGE_INDEX:03d}.png"
    w, h = Image.open(path).size
    if (w, h) != (1691, 531):
        sys.exit(f"unexpected image {path}: {w}x{h}; the PDF is not the one this was calibrated on")
    return path


def watts(y):
    return (Y_0W - y) * 20.0 / (Y_0W - Y_20W)


def read_series(im, rgb):
    """Per column: the colour-weighted centre of the matching line pixels, or NaN."""
    target = np.array(rgb, dtype=float)
    d = np.sqrt(((im[:, X0:X1 + 1, :] - target) ** 2).sum(axis=2))
    rows = np.arange(im.shape[0], dtype=float)[:, None]
    wgt = np.clip(1.0 - d / COLOUR_TOL, 0.0, None)
    wgt[:60, :] = 0.0                 # title
    wgt[440:, :] = 0.0                # axis labels, legend
    tot = wgt.sum(axis=0)
    centre = np.where(tot > 0.3, (wgt * rows).sum(axis=0) / np.maximum(tot, 1e-9), np.nan)
    return watts(centre)


def fill_gaps(v):
    """Linear interpolation across hidden stretches; a stretch that runs to
    the zero line on both sides is zero (the eclipse)."""
    v = v.copy()
    idx = np.arange(len(v))
    ok = ~np.isnan(v)
    v[~ok] = np.interp(idx[~ok], idx[ok], v[ok])
    return np.clip(v, 0.0, None)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("-o", "--outdir", required=True)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        png = extract_png(a.pdf, td)
        png_sha = sha256(png)
        src = Image.open(png).convert("RGB")
        im = np.array(src).astype(float)

        cols = {name: read_series(im, rgb) for name, rgb in SERIES.items()}
        # Eclipse: no series has a line pixel above ~0.4 W. Everything in it
        # is zero by construction, so interpolation must not leak into it.
        above = np.zeros(X1 - X0 + 1, dtype=bool)
        for v in cols.values():
            above |= np.nan_to_num(v, nan=0.0) > 0.4
        for name in cols:
            cols[name] = fill_gaps(cols[name])
        # The longest run with nothing above zero is the eclipse.
        best, run, start, best_start = 0, 0, 0, 0
        for i, on in enumerate(above):
            if not on:
                if run == 0:
                    start = i
                run += 1
                if run > best:
                    best, best_start = run, start
            else:
                run = 0
        ecl = slice(best_start, best_start + best)
        for name in cols:
            cols[name][ecl] = 0.0
        total = sum(cols.values())

        n = len(total)
        phase = np.arange(n) / (n - 1)
        orbit_h = ORBIT_MIN / 60.0
        avg = float(np.trapz(total, phase))
        wh = avg * orbit_h
        ecl_frac = best / (n - 1)

        # Overlay for a visual check.
        ov = src.copy()
        dr = ImageDraw.Draw(ov)
        for name, v in list(cols.items()) + [("total", total)]:
            ys = Y_0W - v * (Y_0W - Y_20W) / 20.0
            pts = [(X0 + i, float(y)) for i, y in enumerate(ys)]
            dr.line(pts, fill=(255, 255, 255) if name == "total" else (0, 255, 255), width=1)
        ov.save(os.path.join(a.outdir, "fig7-3_overlay.png"))

    out = os.path.join(a.outdir, "fig7-3_digitised.csv")
    with open(out, "w", newline="") as fh:
        fh.write(f"# SI-DISCOII-DR-001 Issue 1.0, Figure 7-3, digitised by tools/digitise_fig7_3.py\n")
        fh.write(f"# pdf_sha256={sha256(a.pdf)} image=page{PAGE}#{IMAGE_INDEX} image_sha256={png_sha}\n")
        fh.write(f"# phase 0 = left edge of the plot window (NOT eclipse exit); one orbit = {ORBIT_MIN:.3f} min\n")
        fh.write(f"# eclipse phase {best_start/(n-1):.4f}..{(best_start+best)/(n-1):.4f}\n")
        w = csv.writer(fh)
        w.writerow(["phase", *cols.keys(), "total_w"])
        for i in range(n):
            w.writerow([f"{phase[i]:.5f}", *(f"{cols[k][i]:.3f}" for k in cols), f"{total[i]:.3f}"])

    print(f"wrote {out}")
    print(f"orbit average      {avg:6.2f} W   (report: 17.3 W)")
    print(f"energy per orbit   {wh:6.2f} Wh  (report Fig 7-4: 27.5 Wh)")
    print(f"eclipse            {ecl_frac * ORBIT_MIN:6.2f} min ({ecl_frac:.3f} of the orbit; "
          f"beta=0 geometry for this orbit gives 35.6 min)")
    print(f"sunlit average     {avg / (1 - ecl_frac):6.2f} W   peak {total.max():.2f} W")
    for k, v in cols.items():
        print(f"  {k:14s} peak {v.max():5.2f} W  mean {np.trapz(v, phase):5.2f} W")


if __name__ == "__main__":
    main()
