#!/usr/bin/env python3
"""Build the cost store's seed file (format 2, "CST2") from the flight config.

    seed_cost_store.py --manifest experiments/board/flight-config/manifest.yaml \\
        --hints -o staging/<set>/cost.seed
    seed_cost_store.py --manifest ... --model analysis/cost_model.json -o ...
    seed_cost_store.py --manifest ... --verify-console runs/x/console.log

The daemon keys the store by module CONFIG hash: murmur3_32 of the slot's
decoded bytes, seed 42 (dipp_config.c). Those bytes are exactly what
vmem_codec.encode_module produces from the slot's YAML (the slot holds them
Brotli-compressed), so every hash is computed here, offline, from the same
manifest stage.sh builds storage.vmem from. A YAML edit changes the hash,
which is why stage.sh regenerates the seed every time.

Exactly one source of values, chosen explicitly:

  --hints   one MODEL entry per slot with slope 0 at the YAML hint: the store
            then predicts exactly what the hint says for every input size.
            The plumbing seed -- behaviour identical to an unseeded store,
            but EXEC reports cost_src=model, which proves the seed is read.
  --model   cost_model.json from analysis/cost_model_fit.py:
              {"slots": {"<slot>": {
                  "n_ref": int,
                  "model": {"lat_ref_us", "lat_slope_us", "en_ref_uwh",
                            "en_slope_uwh"} | null,
                  "points": [{"n", "lat_us", "en_uwh"}, ...] }}}
            one POINT per measured n, plus the fitted MODEL where the fit
            produced one. A slot the file does not list gets no entries and
            falls back to its hint on the board -- and the daemon says so
            (cost_src=hint), so that is visible, not silent.

--verify-console checks every computed hash against the daemon's own
"Module Config: ID=k ... Hash=H" lines (ID is the zero-based slot index).
It is the load-bearing Python-vs-C check: exit 1 on any mismatch or
missing slot.

File layout (src/include/cost_store/cost_store.h): a 16-byte header
{magic 0x32545343 "CST2", version 2, n_entries 512, pad}, then 512 entries of
48 bytes, little-endian:
  u64 timestamp | u32 hash | u8 kind | u8 origin | u8 valid | u8 pad |
  i32 n | u32 n_obs | u32 lat_us | f32 lat_slope_us | f32 en_uwh |
  f32 en_slope_uwh | 8 bytes reserved
"""
import argparse, hashlib, json, os, re, struct, sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import vmem_codec  # noqa: E402
from murmur3 import murmur3_32  # noqa: E402

MAGIC, VERSION, MAX_ENTRIES = 0x32545343, 2, 512
KIND_POINT, KIND_MODEL = 1, 2
ORIGIN_SEED = 1
HASH_SEED = 42
ENTRY = struct.Struct("<QIBBBBiIIfff8x")
HEADER = struct.Struct("<IIII")
assert ENTRY.size == 48 and HEADER.size == 16


def slot_hashes(manifest: Path):
    """{slot: (hash, yaml doc)} for every module slot in the manifest."""
    man = yaml.safe_load(manifest.read_text())
    out = {}
    for slot, rel in sorted(man["modules"].items(), key=lambda kv: int(kv[0])):
        doc = yaml.safe_load((manifest.parent / rel).read_text())
        out[int(slot)] = (murmur3_32(vmem_codec.encode_module(doc), HASH_SEED), doc)
    return out


def entry(hash_, kind, n, lat_us, lat_slope, en_uwh, en_slope):
    if lat_us < 0 or en_uwh < 0:
        raise ValueError(f"negative cost for hash {hash_}: lat {lat_us} en {en_uwh}")
    return ENTRY.pack(0, hash_, kind, ORIGIN_SEED, 1, 0, int(n), 0,
                      int(round(lat_us)), float(lat_slope), float(en_uwh), float(en_slope))


def build(hashes, model_path):
    """Entries per CONFIGURATION: two slots with identical YAML (the flight
    config's slots 10 and 14) share a hash, and so share one set of entries."""
    entries, summary, first = [], {}, {}
    for slot, (h, _) in hashes.items():
        first.setdefault(h, slot)
    if model_path is None:
        for slot, (h, doc) in hashes.items():
            if first[h] != slot:
                summary[slot] = {"hash": h, "same_config_as_slot": first[h]}
                continue
            lat, en = int(doc["latency_cost"]), int(doc["energy_cost"])
            if lat <= 0 or en <= 0:
                raise SystemExit(f"slot {slot}: hint not positive (latency {lat}, energy {en}); "
                                 "the daemon would reject this slot")
            entries.append(entry(h, KIND_MODEL, 0, lat, 0.0, en, 0.0))
            summary[slot] = {"hash": h, "points": 0, "model": "hint, slope 0"}
        return entries, summary
    model = json.loads(Path(model_path).read_text())
    unknown = set(map(int, model["slots"])) - set(hashes)
    if unknown:
        raise SystemExit(f"{model_path} lists slots not in the manifest: {sorted(unknown)}")
    for slot, (h, _) in hashes.items():
        if first[h] != slot:
            if str(slot) in model["slots"]:
                raise SystemExit(f"slot {slot} has the same config as slot {first[h]}; "
                                 f"give its measurements under slot {first[h]} only")
            summary[slot] = {"hash": h, "same_config_as_slot": first[h]}
            continue
        spec = model["slots"].get(str(slot))
        if spec is None:
            summary[slot] = {"hash": h, "points": 0, "model": None}
            continue
        for p in spec.get("points", []):
            entries.append(entry(h, KIND_POINT, p["n"], p["lat_us"], 0.0, p["en_uwh"], 0.0))
        m = spec.get("model")
        if m is not None:
            entries.append(entry(h, KIND_MODEL, spec["n_ref"], m["lat_ref_us"], m["lat_slope_us"],
                                 m["en_ref_uwh"], m["en_slope_uwh"]))
        summary[slot] = {"hash": h, "points": len(spec.get("points", [])),
                         "model": None if m is None else f"n_ref {spec['n_ref']}"}
    return entries, summary


def verify_console(hashes, console: Path):
    seen = {}
    for line in console.read_text(errors="replace").splitlines():
        m = re.search(r"Module Config: ID=(\d+),.*Hash=(\d+)", line)
        if m:
            seen[int(m.group(1)) + 1] = int(m.group(2))
    bad = 0
    for slot, (h, _) in hashes.items():
        got = seen.get(slot)
        ok = got == h
        bad += not ok
        print(f"  slot {slot:2d}  computed {h:10d}  daemon {got if got is not None else '(absent)':>10}  "
              f"{'MATCH' if ok else 'MISMATCH'}")
    extra = sorted(set(seen) - set(hashes))
    if extra:
        print(f"  daemon reports slots not in the manifest: {extra}")
        bad += len(extra)
    print(f"verify: {len(hashes) - bad if bad <= len(hashes) else 0}/{len(hashes)} slots match")
    return bad == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, type=Path)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--hints", action="store_true")
    src.add_argument("--model", type=Path)
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--verify-console", type=Path)
    a = ap.parse_args()

    hashes = slot_hashes(a.manifest)
    if a.verify_console:
        return 0 if verify_console(hashes, a.verify_console) else 1

    if not (a.hints or a.model) or not a.out:
        ap.error("building a seed needs --out and exactly one of --hints / --model")
    entries, summary = build(hashes, a.model)
    if len(entries) > MAX_ENTRIES:
        raise SystemExit(f"{len(entries)} entries exceed the store's {MAX_ENTRIES}")
    blob = HEADER.pack(MAGIC, VERSION, MAX_ENTRIES, 0) + b"".join(entries)
    blob += b"\0" * (HEADER.size + MAX_ENTRIES * ENTRY.size - len(blob))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(blob)
    source = "hints" if a.hints else str(a.model)
    side = {"source": source, "manifest": str(a.manifest), "entries": len(entries),
            "md5": hashlib.md5(blob).hexdigest(), "slots": summary}
    a.out.with_suffix(a.out.suffix + ".json").write_text(json.dumps(side, indent=1))
    print(f"wrote {a.out}: {len(entries)} entries from {source}, {len(blob)} bytes, md5 {side['md5']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
