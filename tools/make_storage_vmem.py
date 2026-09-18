#!/usr/bin/env python3
"""
Build or inspect storage.vmem offline.

    make_storage_vmem.py build <manifest.yaml> -o storage.vmem
    make_storage_vmem.py dump  <storage.vmem> [--manifest-out DIR]

The manifest makes a config set a versioned artifact instead of a sequence of
interactive uploads, which is what made the original runs unreproducible:

    pipelines:
      1: yaml/full_pipeline_config.yaml
      2: yaml/full_pipeline_static_low.yaml
    modules:
      1: yaml/demosaic_config.yaml
      3: yaml/classification_low_config.yaml
    radio_node_id: 0        # optional
    module_timeout: 30  # optional, SECONDS (goes straight into alarm())

Paths are resolved relative to the manifest. Verified byte-identical against
the flight configuration recovered from the board.
"""
import sys, argparse, struct, yaml
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import vmem_codec as vc


def build(manifest_path: Path, out: Path) -> int:
    man = yaml.safe_load(manifest_path.read_text())
    base = manifest_path.parent
    img = bytearray(b"\x00" * vc.VMEM_SIZE)
    used = 0

    for kind, encode in (("pipelines", vc.encode_pipeline),
                         ("modules",   vc.encode_module)):
        for idx, rel in (man.get(kind) or {}).items():
            doc = yaml.safe_load((base / rel).read_text())
            payload = encode(doc)
            slot = vc.pack_slot(payload)
            off = vc.slot_offset(kind[:-1], int(idx))
            img[off:off + vc.SLOT] = slot
            used += 1
            print(f"  {kind[:-1]:<8} {idx:>2} @ 0x{off:04X}  "
                  f"{len(payload):>3} proto -> {slot[0]:>3} brotli B   {rel}")

    if "radio_node_id" in man:
        img[vc.OFF_RADIO] = int(man["radio_node_id"]) & 0xFF
    if "module_timeout" in man:
        struct.pack_into("<I", img, vc.OFF_TIMEOUT, int(man["module_timeout"]))
        print(f"  module_timeout = {man['module_timeout']} us @ 0x{vc.OFF_TIMEOUT:04X}")

    out.write_bytes(bytes(img))
    print(f"\nwrote {out} ({len(img)} bytes, {used} slots populated)")
    return 0


def dump(path: Path, manifest_out: Path | None) -> int:
    data = path.read_bytes()
    if len(data) != vc.VMEM_SIZE:
        print(f"warning: {len(data)} bytes, expected {vc.VMEM_SIZE}", file=sys.stderr)
    man = {"pipelines": {}, "modules": {}}

    for i in range(1, vc.N_PIPELINES + 1):
        off = vc.slot_offset("pipeline", i)
        raw = vc.unpack_slot(data[off:off + vc.SLOT])
        if raw is None:
            continue
        d = vc.decode_pipeline(raw)
        print(f"pipeline {i} @ 0x{off:04X}  {data[off]} brotli -> {len(raw)} proto B")
        for m in d["pipeline"]:
            impls = ", ".join(f"{im['param_id']}:{im['effort_level']}"
                              for im in m["implementations"])
            print(f"    {m['name']:<32} [{impls}]")
        if manifest_out:
            p = manifest_out / f"pipeline_{i}.yaml"
            p.write_text(yaml.safe_dump(d, sort_keys=False))
            man["pipelines"][i] = p.name

    for i in range(1, vc.N_MODULES + 1):
        off = vc.slot_offset("module", i)
        raw = vc.unpack_slot(data[off:off + vc.SLOT])
        if raw is None:
            continue
        d = vc.decode_module(raw)
        ps = "; ".join(f"{p['key']}={p['value']}" for p in d["parameters"])
        print(f"module {i:>2} @ 0x{off:04X}  latency={d['latency_cost']}us "
              f"energy={d['energy_cost']}uWh  {ps}")
        if manifest_out:
            p = manifest_out / f"module_{i}.yaml"
            p.write_text(yaml.safe_dump(d, sort_keys=False))
            man["modules"][i] = p.name

    err = struct.unpack_from("<I", data, vc.OFF_ERROR)[0]
    radio = data[vc.OFF_RADIO]
    timeout = struct.unpack_from("<I", data, vc.OFF_TIMEOUT)[0]
    print(f"\nerror_code={err}  radio_node_id={radio}  module_timeout={timeout}")
    if timeout == 0:
        print("  ! module_timeout is 0 -- alarm(0) CANCELS, so MODULE_EXIT_TIMEOUT "
              "is unreachable and module execution is unbounded.")

    if manifest_out:
        man["radio_node_id"] = radio
        man["module_timeout"] = timeout
        (manifest_out / "manifest.yaml").write_text(yaml.safe_dump(man, sort_keys=False))
        print(f"\nwrote manifest and {len(man['pipelines'])+len(man['modules'])} "
              f"YAML files to {manifest_out}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("manifest"); b.add_argument("-o", required=True)
    d = sub.add_parser("dump"); d.add_argument("vmem"); d.add_argument("--manifest-out")
    a = ap.parse_args()
    if a.cmd == "build":
        return build(Path(a.manifest), Path(a.o))
    out = Path(a.manifest_out) if a.manifest_out else None
    if out: out.mkdir(parents=True, exist_ok=True)
    return dump(Path(a.vmem), out)


if __name__ == "__main__":
    sys.exit(main())
