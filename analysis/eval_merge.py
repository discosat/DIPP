#!/usr/bin/env python3
"""Assemble the final evaluation set from the two campaigns (METRIC.md
addendum 2026-10-09 / 2026-10-11).

    python3 analysis/eval_merge.py -o runs/eval_final

runs/eval   (policy v1, 2026-10-07..09; feeder fault in overloaded runs)
runs/eval2  (policy v2 + the feeder fix)

Rules, applied per run label:
  * a label run in eval2 is taken from eval2;
  * an eval run with a late feed (TRACE_END late_frames > 0) is invalid and
    is never used (all of them are in eval2);
  * an eval run of the default policy (Cx-adaptive-sN) that is valid is the
    policy as first evaluated: relabelled Cx-adaptive-v1-sN;
  * eval runs of v1 ablations (adaptive-batch / -k1.5 / -k3) are superseded
    by their v2 versions and dropped;
  * every other valid eval run (statics, greedy, twotier-orig) is kept.
Writes symlinks runs/eval_final/<label> -> source run directory, and a
MERGE.json listing the decision for every source run. Nothing is copied.
"""
import argparse, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def late_frames(d):
    try:
        te = json.load(open(os.path.join(d, "RUN.json"))).get("trace_end", "")
    except Exception:
        return None
    m = re.search(r"late_frames=(\d+)", te)
    return int(m.group(1)) if m else None


def complete(d):
    try:
        return bool(json.load(open(os.path.join(d, "RUN.json"))).get("complete"))
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    e1, e2 = os.path.join(ROOT, "runs/eval"), os.path.join(ROOT, "runs/eval2")
    chosen, log = {}, []
    for lab in sorted(os.listdir(e2)) if os.path.isdir(e2) else []:
        d = os.path.join(e2, lab)
        if not complete(d):
            log.append({"src": d, "decision": "eval2 run not complete: missing from the final set"})
            continue
        if late_frames(d) != 0:
            log.append({"src": d, "decision": f"eval2 run with late feed ({late_frames(d)}): EXCLUDED"})
            continue
        chosen[lab] = d
        log.append({"src": d, "label": lab, "decision": "eval2"})
    for lab in sorted(os.listdir(e1)):
        d = os.path.join(e1, lab)
        if not complete(d):
            log.append({"src": d, "decision": "not complete"})
            continue
        lf = late_frames(d)
        m = re.match(r"^(C\d+)-(.+)-s(\d+)$", lab)
        pol = m.group(2) if m else ""
        if lf != 0:
            log.append({"src": d, "decision": f"invalid: feeder late ({lf})"})
            continue
        if pol in ("adaptive-batch", "adaptive-k1.5", "adaptive-k3"):
            log.append({"src": d, "decision": "v1 ablation superseded by v2"})
            continue
        if pol == "adaptive":
            new = f"{m.group(1)}-adaptive-v1-s{m.group(3)}"
            if new in chosen:
                log.append({"src": d, "decision": f"v1 run, {new} also in eval2: eval2 kept"})
                continue
            chosen[new] = d
            log.append({"src": d, "label": new, "decision": "valid v1 run, relabelled"})
            continue
        if lab in chosen:
            log.append({"src": d, "decision": "superseded by eval2"})
            continue
        chosen[lab] = d
        log.append({"src": d, "label": lab, "decision": "eval (valid, unaffected)"})
    os.makedirs(a.out, exist_ok=True)
    for lab, d in chosen.items():
        p = os.path.join(a.out, lab)
        if os.path.islink(p):
            os.remove(p)
        os.symlink(os.path.relpath(d, a.out), p)
    json.dump({"rules": __doc__, "runs": log}, open(os.path.join(a.out, "MERGE.json"), "w"), indent=1)
    print(f"{len(chosen)} runs -> {a.out}")
    for x in log:
        if "label" not in x:
            print("  ", os.path.relpath(x["src"], ROOT), "--", x["decision"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
