#!/usr/bin/env python3
"""murmur3_32, bit-for-bit the daemon's src/utils/murmur_hash.c.

    tools/murmur3.py FILE [--seed 42]     hash a file's bytes

The daemon identifies a module configuration by murmur3_32(decoded slot bytes,
seed 42) (dipp_config.c), and the cost store is keyed by that hash, so the
seed tool has to compute the same value offline. Two details of the C code
matter and are reproduced here: blocks are read as little-endian uint32 (the
board is little-endian and the C casts the byte pointer), and the tail switch
falls through. tests/test_seed_cost_store.py checks this against the C source
on random inputs.
"""
import argparse, struct, sys

M32 = 0xFFFFFFFF


def _rotl(x, r):
    return ((x << r) | (x >> (32 - r))) & M32


def murmur3_32(data: bytes, seed: int) -> int:
    c1, c2 = 0xCC9E2D51, 0x1B873593
    h = seed & M32
    n_blocks = len(data) // 4
    for (k,) in struct.iter_unpack("<I", data[: n_blocks * 4]):
        k = (k * c1) & M32
        k = _rotl(k, 15)
        k = (k * c2) & M32
        h ^= k
        h = _rotl(h, 13)
        h = (h * 5 + 0xE6546B64) & M32
    tail = data[n_blocks * 4:]
    k1 = 0
    if len(tail) >= 3:
        k1 ^= tail[2] << 16
    if len(tail) >= 2:
        k1 ^= tail[1] << 8
    if len(tail) >= 1:
        k1 ^= tail[0]
        k1 = (k1 * c1) & M32
        k1 = _rotl(k1, 15)
        k1 = (k1 * c2) & M32
        h ^= k1
    h ^= len(data) & M32
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & M32
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & M32
    h ^= h >> 16
    return h


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--seed", type=int, required=True)
    a = ap.parse_args()
    print(murmur3_32(open(a.file, "rb").read(), a.seed))
