#!/usr/bin/env python3
"""Adversarial search for a policy's worst scenarios: hill-climbing over the
policy_lab scenario parameters to maximise regret against the best static
policy. Starts from the worst scenarios of a sweep plus random ones.

    python3 analysis/lab_adversary.py --policy C3 --start analysis/lab/test2_exact.csv \
        --chains 32 --iters 60 -o analysis/lab/adversary_C3.csv
"""
import argparse, csv, math, multiprocessing as mp, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import policy_lab as L  # noqa: E402

SPACE = {  # name: (lo, hi, log)
    "period": (1.8, 12.0, True), "burst_mean": (3, 600, True), "burst_cv": (0.0, 1.5, False),
    "gap": (15, 6000, True), "slo_r": (60, 2400, True), "urgent": (0.0, 0.4, False),
    "water": (0.05, 1.0, False), "clear": (0.0, 1.0, False), "hi_scale": (1.0, 2.0, False),
    "arrival_cv": (0.03, 1.0, True),
}
VARS = ("const", "per_burst", "shift")
POLICY = None
PSET = "c3"


def policies(name):
    return [p for p in L.policy_set(PSET) if p.name.startswith("static")] + \
           [p for p in L.policy_set(PSET) if p.name == name]


def evaluate(params, seed):
    sc = L.make_scenario(seed, **params)
    U = {p.name: L.simulate(sc, p)["U"] for p in policies(POLICY)}
    best = max(U[f"static-{e}"] for e in ("low", "med", "high"))
    return best - U[POLICY], U


def perturb(params, rng):
    q = dict(params)
    for k in rng.sample(list(SPACE) + ["content_var"], rng.randint(1, 3)):
        if k == "content_var":
            q[k] = rng.choice(VARS)
            continue
        lo, hi, lg = SPACE[k]
        if lg:
            v = math.exp(math.log(max(q[k], lo)) + rng.gauss(0, 0.3))
        else:
            v = q[k] + rng.gauss(0, 0.15 * (hi - lo))
        q[k] = min(hi, max(lo, v))
    return q


def chain(args):
    i, start, seed, iters = args
    rng = random.Random(1000 + i)
    cur, s = dict(start), seed
    best, U = evaluate(cur, s)
    for _ in range(iters):
        cand = perturb(cur, rng)
        s2 = s if rng.random() < 0.8 else rng.randint(300000, 399999)
        r, u = evaluate(cand, s2)
        if r > best:
            cur, s, best, U = cand, s2, r, u
    return {"chain": i, "regret": round(best, 4), "seed": s, **{k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in cur.items()}, **{f"U:{k}": round(v, 4) for k, v in U.items()}}


def main():
    global POLICY, PSET
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--set", default="c3", help="policy_lab policy set holding --policy")
    ap.add_argument("--slo-max", type=float, default=2400.0, help="upper end of the routine deadline range")
    ap.add_argument("--start", required=True)
    ap.add_argument("--chains", type=int, default=32)
    ap.add_argument("--iters", type=int, default=60)
    ap.add_argument("-o", required=True)
    a = ap.parse_args()
    POLICY, PSET = a.policy, a.set
    SPACE["slo_r"] = (60, a.slo_max, True)      # workers are forked after this: they inherit it
    rows = list(csv.DictReader(open(a.start)))
    for r in rows:
        r["_R"] = max(float(r[f"U:static-{e}"]) for e in ("low", "med", "high")) - float(r[f"U:{a.policy}"])
    rows.sort(key=lambda r: -r["_R"])
    keys = list(SPACE) + ["content_var"]
    starts = []
    for r in rows[: a.chains // 2]:
        p = {k: (r[k] if k == "content_var" else float(r[k])) for k in keys if k in r}
        p.setdefault("arrival_cv", 0.03)
        starts.append((p, int(float(r["seed"]))))
    rng = random.Random(7)
    while len(starts) < a.chains:
        p = {k: (math.exp(rng.uniform(math.log(lo), math.log(hi))) if lg else rng.uniform(lo, hi))
             for k, (lo, hi, lg) in SPACE.items()}
        p["content_var"] = rng.choice(VARS)
        starts.append((p, rng.randint(600000, 699999)))   # outside every sweep's seed range
    with mp.Pool(8, initializer=_init, initargs=(a.policy,)) as pool:
        out = pool.map(chain, [(i, p, s, a.iters) for i, (p, s) in enumerate(starts)])
    out.sort(key=lambda r: -r["regret"])
    with open(a.o, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader(); w.writerows(out)
    for r in out[:10]:
        print(f"R {r['regret']:.3f} | " + " ".join(f"{k}={r[k]}" for k in keys))
    print(f"wrote {a.o}")
    return 0


def _init(p):
    global POLICY
    POLICY = p


if __name__ == "__main__":
    sys.exit(main())
