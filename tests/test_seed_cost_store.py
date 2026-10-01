#!/usr/bin/env python3
"""Host test: tools/murmur3.py and tools/seed_cost_store.py against the C code.

    python3 tests/test_seed_cost_store.py      (from the repository root)

Compiles two tiny programs from the daemon's own sources with the host gcc:
one hashes stdin with src/utils/murmur_hash.c, the other loads a seed file
with the MEM cost-store backend and prints cost_model_predict for a (hash, n,
hint). Then checks that

  * murmur3.py equals the C on 500 random inputs of every length mod 4,
  * a --hints seed makes every configured slot predict its hint at any n,
    reported as cost_src=model (the seed is read),
  * a --model seed gives the measured POINT at its own n, the fitted line
    elsewhere, and the hint for a slot the model file does not list,
  * two slots with the same configuration produce one set of entries,
  * the file is exactly 16 + 512 x 48 bytes with the CST2 header.

On the board, --verify-console is the complementary check: 19/19 slot hashes
matched the daemon's own on 2026-09-30.
"""
import json, os, random, struct, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from murmur3 import murmur3_32  # noqa: E402
import seed_cost_store as scs  # noqa: E402

MANIFEST = ROOT / "experiments/board/flight-config/manifest.yaml"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  {name:<66} {'PASS' if ok else 'FAIL'}{('  ' + detail) if detail and not ok else ''}")


HASH_C = r'''
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
uint32_t murmur3_32(const uint8_t *key, size_t len, uint32_t seed);
int main(int argc, char **argv) {
    static uint8_t buf[1 << 16];
    size_t n = fread(buf, 1, sizeof buf, stdin);
    printf("%u\n", murmur3_32(buf, n, (uint32_t)strtoul(argv[1], 0, 10)));
    return 0;
}
'''

PREDICT_C = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cost_store.h"
#include "cost_model.h"
CostStore *cost_store = NULL;
CostStoreImpl *cost_store_impl = NULL;
int main(int argc, char **argv) {
    /* argv: seedfile, then triples hash n hint_lat */
    cost_store_impl = &cost_store_mem;
    if (cost_store_impl->init(&cost_store, argv[1]) != 0) return 2;
    int n_valid = 0;
    for (int i = 0; i < MAX_ENTRIES; i++) n_valid += cost_store->items[i].valid;
    printf("valid %d\n", n_valid);
    for (int i = 2; i + 2 < argc; i += 3) {
        ModuleParameterList cfg; memset(&cfg, 0, sizeof cfg);
        cfg.hash = (uint32_t)strtoul(argv[i], 0, 10);
        cfg.latency_cost = (uint32_t)strtoul(argv[i + 2], 0, 10);
        cfg.energy_cost = 1;
        uint32_t lat; float en;
        CostSource s = cost_model_predict(&cfg, atoi(argv[i + 1]), &lat, &en);
        printf("%s %u %.3f\n", cost_source_name(s), lat, en);
    }
    return 0;
}
'''


def gcc(src, extra, out, work):
    c = work / (out.name + ".c")
    c.write_text(src)
    cmd = ["gcc", "-O1", "-o", str(out), str(c), *extra,
           "-I", str(ROOT / "src/include"), "-I", str(ROOT / "src/include/cost_store"),
           "-I", str(ROOT / "src/include/image")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr)
        raise SystemExit("compile failed")


def predict(prog, seed, queries):
    args = [str(prog), str(seed)]
    for h, n, hint in queries:
        args += [str(h), str(n), str(hint)]
    out = subprocess.run(args, capture_output=True, text=True).stdout.split("\n")
    valid = int([l for l in out if l.startswith("valid")][0].split()[1])
    rows = [l.split() for l in out if l and not l.startswith(("valid", "COST_STORE"))]
    return valid, [(r[0], int(r[1]), float(r[2])) for r in rows]


def main():
    print("murmur3.py / seed_cost_store.py vs the C sources")
    work = Path(tempfile.mkdtemp())
    hash_prog, pred_prog = work / "hash", work / "predict"
    gcc(HASH_C, [str(ROOT / "src/utils/murmur_hash.c")], hash_prog, work)
    gcc(PREDICT_C, [str(ROOT / f) for f in ("src/cost_store/cost_model.c", "src/cost_store/cost_store.c",
                                            "src/cost_store/cost_store_mem.c",
                                            "src/cost_store/cost_store_mmap.c")], pred_prog, work)

    rng = random.Random(7)
    bad = 0
    for i in range(500):
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 300)))
        seed = rng.choice([0, 42, 0xFFFFFFFF, rng.randrange(1 << 32)])
        c = int(subprocess.run([str(hash_prog), str(seed)], input=data, capture_output=True).stdout)
        bad += c != murmur3_32(data, seed)
    check("murmur3.py equals the C on 500 random inputs", bad == 0, f"{bad} differ")

    hashes = scs.slot_hashes(MANIFEST)

    # --hints
    seed = work / "hints.seed"
    subprocess.run([sys.executable, str(ROOT / "tools/seed_cost_store.py"), "--manifest", str(MANIFEST),
                    "--hints", "-o", str(seed)], check=True, capture_output=True)
    blob = seed.read_bytes()
    check("file is 16 + 512 x 48 bytes with the CST2 header",
          len(blob) == 16 + 512 * 48 and struct.unpack_from("<III", blob) == (0x32545343, 2, 512))
    distinct = len({h for h, _ in hashes.values()})
    queries = [(h, n, doc["latency_cost"]) for h, doc in hashes.values() for n in (1, 7, 99, 400)]
    valid, rows = predict(pred_prog, seed, queries)
    want = [("model", doc["latency_cost"]) for h, doc in hashes.values() for _ in range(4)]
    check(f"--hints: one entry per distinct config ({distinct} for {len(hashes)} slots)", valid == distinct,
          f"valid {valid}")
    check("--hints: every slot predicts its hint at any n, as cost_src=model",
          [(r[0], r[1]) for r in rows] == want)

    # --model
    s3, s5 = hashes[3][0], hashes[5][0]
    model = {"slots": {
        "3": {"n_ref": 99, "points": [{"n": 99, "lat_us": 500000, "en_uwh": 200.0},
                                      {"n": 198, "lat_us": 950000, "en_uwh": 390.0}],
              "model": {"lat_ref_us": 480000, "lat_slope_us": 4500.0,
                        "en_ref_uwh": 195.0, "en_slope_uwh": 2.0}},
        "5": {"n_ref": 99, "points": [], "model": {"lat_ref_us": 1800000, "lat_slope_us": 18000.0,
                                                   "en_ref_uwh": 850.0, "en_slope_uwh": 8.5}},
    }}
    mfile = work / "cost_model.json"
    mfile.write_text(json.dumps(model))
    seed = work / "model.seed"
    subprocess.run([sys.executable, str(ROOT / "tools/seed_cost_store.py"), "--manifest", str(MANIFEST),
                    "--model", str(mfile), "-o", str(seed)], check=True, capture_output=True)
    valid, rows = predict(pred_prog, seed, [(s3, 99, 1), (s3, 198, 1), (s3, 50, 1), (s5, 16, 1),
                                           (hashes[7][0], 16, 4158100)])
    check("--model: 3 entries for slot 3 (2 points + line) and 1 for slot 5", valid == 4, f"valid {valid}")
    check("--model: a measured point answers at its own n", rows[0][:2] == ("point", 500000) and
          rows[1][:2] == ("point", 950000), str(rows[:2]))
    check("--model: the fitted line answers between points (n=50 -> 259,500 us)",
          rows[2][:2] == ("model", 480000 + 4500 * (50 - 99)), str(rows[2]))
    check("--model: a line with no points still answers (slot 5, n=16 -> 306,000 us)",
          rows[3][:2] == ("model", 1800000 + 18000 * (16 - 99)), str(rows[3]))
    check("--model: a slot absent from the model falls back to its hint, visibly",
          rows[4][:2] == ("hint", 4158100), str(rows[4]))

    bad_model = {"slots": {"14": {"n_ref": 1, "points": [], "model": None}}}
    mfile.write_text(json.dumps(bad_model))
    r = subprocess.run([sys.executable, str(ROOT / "tools/seed_cost_store.py"), "--manifest", str(MANIFEST),
                        "--model", str(mfile), "-o", str(work / "x.seed")], capture_output=True, text=True)
    check("refuses measurements given under a duplicate-config slot", r.returncode != 0)
    r = subprocess.run([sys.executable, str(ROOT / "tools/seed_cost_store.py"), "--manifest", str(MANIFEST),
                        "-o", str(work / "y.seed")], capture_output=True, text=True)
    check("refuses to build without --hints or --model (no default source)", r.returncode != 0)

    failed = sum(1 for _, ok in RESULTS if not ok)
    print("OK (0 failures)" if not failed else f"FAILED ({failed} failures)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
