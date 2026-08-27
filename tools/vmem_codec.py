#!/usr/bin/env python3
"""
storage.vmem codec: YAML <-> protobuf <-> Brotli <-> 188-byte slots.

Layout (src/include/vmem/vmem_storage.h):
    pipelines 1..6   0x000 .. 0x3AC   188 B apart
    modules   1..20  0x468 .. 0x125C  188 B apart
    error code       0x1318  uint32
    radio node id    0x131C  uint8
    module timeout   0x131D  uint32

Slot framing (src/dipp_config.c get_param_buffer):
    byte 0      = length of the Brotli stream
    bytes 1..n  = Brotli-compressed protobuf
Both the compressed AND decompressed forms must fit 188 bytes: the decoder
decompresses into a DATA_PARAM_SIZE stack buffer.

proto3 wire format is implemented inline so the tool needs no protoc.
"""
import sys, struct, brotli

SLOT = 188
PIPELINE_BASE, N_PIPELINES = 0x000, 6
MODULE_BASE,   N_MODULES   = 0x468, 20
OFF_ERROR, OFF_RADIO, OFF_TIMEOUT = 0x1318, 0x131C, 0x131D
VMEM_SIZE = 10000

EFFORT = {"default": 0, "low": 1, "medium": 2, "high": 3}
EFFORT_NAME = {v: k for k, v in EFFORT.items()}

# ---------- proto3 primitives ----------
def _varint(n):
    if n < 0:                       # int32 negatives are 10-byte varints
        n += 1 << 64
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)

def _tag(field, wire):
    return _varint((field << 3) | wire)

def _ld(field, payload):            # length-delimited
    return _tag(field, 2) + _varint(len(payload)) + payload

def _vi(field, value):              # plain scalar: proto3 omits the default
    return b"" if value == 0 else _tag(field, 0) + _varint(value)

def _vi_always(field, value):       # oneof member: ALWAYS emitted, even when 0,
    return _tag(field, 0) + _varint(value)   # or the oneof case is lost

def _read_varint(buf, i):
    shift = res = 0
    while True:
        b = buf[i]; i += 1
        res |= (b & 0x7F) << shift
        if not b & 0x80:
            return res, i
        shift += 7

def _fields(buf):
    """Yield (field_number, wire_type, value) for one message."""
    i = 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        f, w = key >> 3, key & 7
        if w == 0:
            v, i = _read_varint(buf, i)
        elif w == 2:
            n, i = _read_varint(buf, i)
            v, i = buf[i:i + n], i + n
        elif w == 5:
            v, i = struct.unpack("<f", buf[i:i + 4])[0], i + 4
        else:
            raise ValueError(f"unsupported wire type {w}")
        yield f, w, v

# ---------- encode ----------
def encode_pipeline(doc):
    # field 1 (n_module_configs) is deliberately NOT written: nothing in src/
    # reads it, and the encoder that produced the flight configs omits it too.
    # Emitting it would make our slots differ from the deployed ones.
    mods = doc["pipeline"]
    out = b""
    for m in mods:
        body = _ld(1, m["name"].encode())
        for impl in m.get("implementations", []):
            lvl = impl.get("effort_level", "default")
            lvl = EFFORT[lvl] if isinstance(lvl, str) else int(lvl)
            body += _ld(2, _vi(1, int(impl["param_id"])) + _vi(2, lvl))
        out += _ld(2, body)
    return out

def encode_module(doc):
    out = _vi(1, int(doc.get("latency_cost", 0)))
    out += _vi(2, int(doc.get("energy_cost", 0)))
    for p in doc.get("parameters", []):
        t, val = int(p["type"]), p["value"]
        body = _ld(1, str(p["key"]).encode())
        if t == 2:                                        # bool
            body += _vi_always(2, 1 if val else 0)
        elif t == 3:                                      # int32
            body += _vi_always(3, int(val))
        elif t == 4:                                      # float
            body += _tag(4, 5) + struct.pack("<f", float(val))
        elif t == 5:                                      # string
            body += _ld(5, str(val).encode())
        else:
            raise ValueError(f"unknown parameter type {t}")
        out += _ld(4, body)
    return out

# ---------- decode ----------
def decode_pipeline(buf):
    doc = {"pipeline": []}
    for f, w, v in _fields(buf):
        if f == 2:
            mod = {"name": None, "implementations": []}
            for mf, mw, mv in _fields(v):
                if mf == 1:
                    mod["name"] = mv.decode()
                elif mf == 2:
                    impl = {"param_id": 0, "effort_level": "default"}
                    for pf, pw, pv in _fields(mv):
                        if pf == 1: impl["param_id"] = pv
                        elif pf == 2: impl["effort_level"] = EFFORT_NAME.get(pv, pv)
                    mod["implementations"].append(impl)
            doc["pipeline"].append(mod)
    return doc

def decode_module(buf):
    doc = {"latency_cost": 0, "energy_cost": 0, "parameters": []}
    for f, w, v in _fields(buf):
        if f == 1: doc["latency_cost"] = v
        elif f == 2: doc["energy_cost"] = v
        elif f == 4:
            p = {"key": None, "type": None, "value": None}
            for pf, pw, pv in _fields(v):
                if pf == 1: p["key"] = pv.decode()
                elif pf == 2: p.update(type=2, value=bool(pv))
                elif pf == 3: p.update(type=3, value=pv)
                elif pf == 4: p.update(type=4, value=pv)
                elif pf == 5: p.update(type=5, value=pv.decode())
            doc["parameters"].append(p)
    return doc

# ---------- slot framing ----------
def pack_slot(payload: bytes) -> bytes:
    comp = brotli.compress(payload, quality=11)
    if len(comp) > SLOT - 1:
        raise ValueError(f"compressed {len(comp)} B exceeds slot ({SLOT-1} B max)")
    if len(payload) > SLOT:
        raise ValueError(f"decompressed {len(payload)} B exceeds the decoder's "
                         f"{SLOT} B output buffer")
    return bytes([len(comp)]) + comp + b"\x00" * (SLOT - 1 - len(comp))

def unpack_slot(slot: bytes):
    if not any(slot):
        return None
    n = slot[0]
    return brotli.decompress(bytes(slot[1:1 + n]))

def slot_offset(kind, index):
    if kind == "pipeline":
        assert 1 <= index <= N_PIPELINES
        return PIPELINE_BASE + (index - 1) * SLOT
    assert 1 <= index <= N_MODULES
    return MODULE_BASE + (index - 1) * SLOT
