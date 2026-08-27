#!/usr/bin/env python3
"""
Table V: module sizes for uplink.

Regenerates the table from the deployed .so files instead of transcribing it.
Three corrections over the published version:
  1. measure STRIPPED binaries -- the published figures include debug symbols,
     which are never uplinked;
  2. apply Brotli, which is what the uplink path actually does;
  3. count BOTH inference modules (classification and segmentation), not one.

Stripping is runtime-neutral: .debug_* are non-ALLOC, so PT_LOAD and .text are
untouched. The script asserts this rather than asserting it in prose.
"""
import subprocess, sys, shutil, tempfile, os, hashlib
from pathlib import Path

MODDIR = Path(sys.argv[1] if len(sys.argv) > 1
              else "/home/roba/dipp2-experiments/2025-11-18/modules")
STRIP  = "aarch64-linux-gnu-strip"
OBJCOPY= "aarch64-linux-gnu-objcopy"

# The five-stage decomposed pipeline, then the monolith it is compared against.
PIPELINE = [
    ("demosaic",                   "libdemosaic.so"),
    ("tile",                       "libtile.so"),
    ("tflite_classification",      "libtflite_classification.so"),
    ("tflite_binary_segmentation", "libtflite_binary_segmentation.so"),
    ("jpegxl_encode",              "libjpegxl_encode.so"),
]
MONOLITH = ("single_module", "libsingle_module.so")
OTHER = [("resize", "libresize.so"), ("jpegxl_module", "libjpegxl_module.so"),
         ("crash_and_burn", "libcrash_and_burn.so")]

# UHF uplink, the only operational uplink on DISCO-2 (SI-DISCOII-DR-001 7.5)
UPLINK_BPS  = 9600
PASS_SEC    = 480

def brotli_size(data: bytes) -> int:
    import brotli
    return len(brotli.compress(data, quality=11))

def text_digest(path: Path) -> str:
    """SHA-256 of .text only -- must be identical before and after stripping."""
    with tempfile.NamedTemporaryFile(delete=False) as t:
        tmp = t.name
    subprocess.run([OBJCOPY, "-O", "binary", "--only-section=.text",
                    str(path), tmp], check=True)
    d = hashlib.sha256(Path(tmp).read_bytes()).hexdigest()
    os.unlink(tmp)
    return d

def measure(path: Path):
    raw = path.stat().st_size
    with tempfile.TemporaryDirectory() as d:
        cp = Path(d) / path.name
        shutil.copy2(path, cp)
        before = text_digest(cp)
        subprocess.run([STRIP, "--strip-all", str(cp)], check=True)
        after = text_digest(cp)
        assert before == after, f"{path.name}: strip changed .text -- NOT runtime-neutral"
        stripped = cp.stat().st_size
        comp = brotli_size(cp.read_bytes())
    return raw, stripped, comp

def pass_frac(nbytes):
    return nbytes * 8 / UPLINK_BPS / PASS_SEC

def main():
    rows = []
    for label, fn in PIPELINE + [MONOLITH] + OTHER:
        p = MODDIR / fn
        if not p.exists():
            print(f"  ! missing {fn}", file=sys.stderr); continue
        rows.append((label, fn) + measure(p))

    by = {r[0]: r for r in rows}
    mono = by[MONOLITH[0]]
    pipe = [by[l] for l, _ in PIPELINE if l in by]

    print("## Table V (corrected) -- module sizes for uplink\n")
    print("| Module | Raw | Stripped | +Brotli | Brotli/raw | Uplink pass |")
    print("|---|---:|---:|---:|---:|---:|")
    for label, fn, raw, st, br in rows:
        print(f"| `{label}` | {raw:,} | {st:,} | {br:,} | "
              f"{br/raw*100:.1f}% | {pass_frac(br)*100:.1f}% |")

    pipe_br  = sum(r[4] for r in pipe)
    pipe_raw = sum(r[2] for r in pipe)
    print(f"\n**Decomposed pipeline total (5 modules, both inference stages):** "
          f"{pipe_raw:,} raw -> **{pipe_br:,}** stripped+brotli")
    print(f"**Monolith:** {mono[2]:,} raw -> **{mono[4]:,}** stripped+brotli")
    print(f"\nSo the whole monolith is {pass_frac(mono[4])*100:.1f}% of one "
          f"{PASS_SEC}s pass at {UPLINK_BPS} bit/s "
          f"(~{mono[4]*8/UPLINK_BPS:.0f} s of uplink), and swapping the single "
          f"largest stage costs ~{max(r[4] for r in pipe)*8/UPLINK_BPS:.0f} s.")

    print("\n### Per-module share of the monolith (the published metric)\n")
    print("| Module | Raw share | Stripped+Brotli share |")
    print("|---|---:|---:|")
    for label, fn, raw, st, br in pipe:
        print(f"| `{label}` | {raw/mono[2]*100:.1f}% | {br/mono[4]*100:.1f}% |")
    rs = [r[2]/mono[2]*100 for r in pipe]
    bs = [r[4]/mono[4]*100 for r in pipe]
    print(f"\n**Range: {min(rs):.0f}-{max(rs):.0f}% raw becomes "
          f"{min(bs):.0f}-{max(bs):.0f}% stripped+compressed.**")
    print(f"\nSum of the decomposed modules is {pipe_br/mono[4]:.1f}x the "
          f"monolith compressed ({pipe_br:,} vs {mono[4]:,} B), because each "
          f"module re-includes its own copy of the shared utility code.")

if __name__ == "__main__":
    main()
