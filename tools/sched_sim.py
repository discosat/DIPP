#!/usr/bin/env python3
"""Host simulation of the SIPP scheduler on an arrival trace -- for choosing
experiments before bench time, never as a result.

    tools/sched_sim.py --trace experiments/traces/main-s1.trace --content matched \\
        --policy shipped --granularity batch --batch-overhead-s 0.3 --ckpt-s 0.05

What it models (src/dipp_process.c, src/heuristics/*, src/pipeline/pipeline_executor.c):
  * two queues ordered by deadline: ingest (cap MAX_QUEUE_SIZE 100, arrivals
    dropped when full) and partially processed (same cap; MAX_PARTIAL_QUEUE_SIZE
    10 only limits parking by yield and the batch-mode drain);
    arrivals are admitted only between batches (the daemon drains its message
    queue between batches);
  * main loop: run the partial head; if it made no progress, run the ingest
    head; after partial progress also run one ingest batch;
  * before each batch: update_heuristic -- total depth < Q -> best effort,
    else lowest effort;
  * per effort module: budget = (deadline - now) / modules left (including
    this one). best effort: high if lat <= budget; else medium only if
    budget < gate_med; else low only if budget < gate_low (the lowest level
    bypasses the latency check); otherwise NOT_FOUND -> the batch halts and
    moves to the partial queue (stays in ingest if partial is full) and is
    retried every loop pass (HALT_POLL 0.2 s when nothing can run). Lowest
    effort: always the lowest level. Default-effort modules always run;
  * --granularity batch: a started batch runs all remaining modules (shipped);
    --granularity stage: the scheduler returns after every module, picks the
    earliest deadline across both queues, a preempted batch is parked in the
    partial queue paying --ckpt-s (falls back to running on if partial is full);
  * service time per module = cost-model latency (analysis/cost_model.json) at
    the frame's real tile count for the chosen efforts (per-frame counts from
    the size sweep and the Table VII board runs), + --batch-overhead-s once
    per batch (ingest, hand-offs, upload).
Not modelled: energy (BOL, board per burst, 2-orbit floor: the projection
shows it does not bind on these traces), cost-model error, module failures.

Policies: static-low|static-med|static-high (fixed effort; pipelines 2/6/3),
shipped (Q 5, gates 5 s / 3 s), greedy (Q inf), nogate (Q 5, gates inf:
high -> medium -> low by latency), ladder (Q1/Q2 three tiers, reference).
Quality q of a frame = mean over cls/seg/enc of quality(effort)/quality(high).
"""
import argparse, bisect, csv, glob, json, os, re, statistics, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

EFF = ("low", "med", "high")
SLOT = {"cls": {"low": 3, "med": 4, "high": 5}, "seg": {"low": 6, "med": 19, "high": 7},
        "enc": {"low": 16, "med": 17, "high": 18}}
QUAL = {"cls": {"low": .763, "med": .935, "high": 1.0}, "seg": {"low": .752, "med": .960, "high": 1.0},
        "enc": {"low": .858, "med": .951, "high": 1.0}}
MAX_INGEST, MAX_PARTIAL, HALT_POLL = 100, 10, 0.2
MODULES = ("demosaic", "tile", "cls", "seg", "enc")


# ------------------------------------------------------------------ cost model
class Cost:
    def __init__(self, path):
        self.s = json.load(open(path))["slots"]

    def lat(self, slot, n):
        e = self.s[str(slot)]
        pts = {p["n"]: p["lat_us"] for p in e["points"]}
        if n in pts:
            return pts[n] / 1e6
        m = e["model"]
        v = (m["lat_ref_us"] + m["lat_slope_us"] * (n - e["n_ref"])) / 1e6
        return max(v, 0.05)


# ------------------------------------------------------------------ content
def build_content():
    """Per frame: n_seg[cls effort] and n_enc[seg effort], measured on the board.
    Sources: size-sweep k=1 runs (pipelines 2/6/3 = all-low/med/high) and the
    Table VII adaptive runs (mixed low/high) for the matched frames 01..09."""
    pl_eff = {2: "low", 6: "med", 3: "high"}
    sweep = defaultdict(dict)
    for p in glob.glob(os.path.join(ROOT, "analysis/size-sweep/*-k1.json")):
        r = json.load(open(p))
        pl = r["spec"]["pipeline"]
        if pl not in pl_eff:
            continue
        img = r["spec"]["image"].replace(".bayerRG", "")
        by = defaultdict(list)
        for x in r["execs"]:
            by[x["uuid"]].append(x)
        xs = sorted(next(iter(by.values())), key=lambda e: e["ts_us"])
        n = [x["n_input"] for x in xs]
        if len(n) == 5:
            sweep[img][pl_eff[pl]] = (n[3], n[4])         # (n_seg, n_enc)
    content = {}
    for img, d in sweep.items():
        if len(d) == 3:
            content[img] = {"nseg": {e: d[e][0] for e in EFF},
                            "nenc_ratio": {e: d[e][1] / max(d[e][0], 1) for e in EFF}}
    # matched 01..09 from the board runs (param_id 2/4 = cls low/high, 5/6 = seg low/high)
    cls_e = {"2": "low", "4": "high"}
    seg_e = {"5": "low", "6": "high"}
    img_of = {}
    for d in glob.glob(os.path.join(ROOT, "runs/table7-core/t7-adaptive-s*")):
        run = os.path.basename(d)
        if os.path.exists(d + "/feed.log"):
            for m in re.finditer(r"scene=(\d+) .*?image=(\S+)", open(d + "/feed.log").read()):
                img_of[(run, m.group(1))] = m.group(2).replace(".bayerRG", "")
    pb = os.path.join(ROOT, "analysis/parsed/table7-core")
    if os.path.exists(pb + "/batches.csv"):
        B = {(b["run_id"], b["uuid"]): b for b in csv.DictReader(open(pb + "/batches.csv"))}
        E = defaultdict(list)
        for e in csv.DictReader(open(pb + "/executions.csv")):
            E[(e["run_id"], e["uuid"])].append(e)
        obs = defaultdict(lambda: {"nseg": defaultdict(list), "nenc": defaultdict(list)})
        for k, xs in E.items():
            b = B.get(k)
            img = img_of.get((k[0], b.get("scene", ""))) if b else None
            if not img or not img.startswith("cs12_test_matched_"):
                continue
            xs.sort(key=lambda e: int(e["ts_us"]))
            c = [e for e in xs if "classification" in e["module"]]
            s = [e for e in xs if "segmentation" in e["module"]]
            n = [e for e in xs if "jpeg" in e["module"]]
            if c and s and c[0]["param_id"] in cls_e:
                obs[img]["nseg"][cls_e[c[0]["param_id"]]].append(int(s[0]["n_input"]))
            if s and n and s[0]["param_id"] in seg_e:
                obs[img]["nenc"][seg_e[s[0]["param_id"]]].append(
                    int(n[0]["n_input"]) / max(int(s[0]["n_input"]), 1))
        ref = content.get("cs12_test_matched_00")
        for img, o in obs.items():
            if img in content or not ref:
                continue
            nseg = {e: statistics.median(o["nseg"][e]) for e in ("low", "high") if o["nseg"][e]}
            rat = {e: statistics.median(o["nenc"][e]) for e in ("low", "high") if o["nenc"][e]}
            if len(nseg) == 2 and len(rat) == 2:
                nseg["med"] = round((nseg["low"] + nseg["high"]) / 2)
                rat["med"] = ref["nenc_ratio"]["med"]
                content[img] = {"nseg": nseg, "nenc_ratio": rat}
    sets = {
        "matched": sorted(k for k in content if k.startswith("cs12_test_matched_")),
        "keepall": sorted(k for k in content if k.startswith("cs12_test_matchedkeep_")),
        "cloudy": sorted(k for k in content if k.startswith("cs12_validation_")),
    }
    return content, sets


# ------------------------------------------------------------------ trace
def read_trace(path):
    out, ff = [], 0.0
    for ln in open(path):
        if ln.startswith("#") or not ln.strip():
            continue
        t, f, scene, dt, slo, cls = ln.split()
        ff += float(f)
        out.append((float(t) + ff, float(slo), cls))
    return out


# ------------------------------------------------------------------ simulation
class Batch:
    __slots__ = ("i", "arr", "dl", "cls", "frame", "prog", "eff", "done", "halted", "parked")

    def __init__(self, i, arr, slo, cls, frame):
        self.i, self.arr, self.dl, self.cls, self.frame = i, arr, arr + slo, cls, frame
        self.prog, self.eff, self.done, self.halted, self.parked = -1, {}, None, 0, 0


def simulate(arrivals, frames, content, cost, policy, gran, over, ckpt, q=5, gmed=5.0, glow=3.0,
             q1=12, q2=20, rho_window=8, lat_scale=1.0, ingest_s=0.0, rho_rate="window", edf_margin=0.0, slo_routine=300.0, k_rule=2.0, hist_gap=None, hist_window=None):
    if policy == "greedy":
        q = float("inf")
    if policy == "nogate":
        gmed = glow = float("inf")
    batches = [Batch(i, a, s, c, frames[i % len(frames)]) for i, (a, s, c) in enumerate(arrivals)]
    ingest, partial, dropped = [], [], []
    t, nxt, preempts, maxdepth = 0.0, 0, 0, 0

    drains = []                                   # (time, frames found) per drain that found frames
    idle_arrival = []                             # per admitted frame: did it find the system empty?
    depth_at_arrival = []                         # per admitted frame: frames already queued

    def admit():
        nonlocal nxt, t
        k0 = nxt
        while nxt < len(batches) and batches[nxt].arr <= t + 1e-9:
            b = batches[nxt]; nxt += 1
            idle_arrival.append(not ingest and not partial)   # arrives to an empty system
            depth_at_arrival.append(len(ingest) + len(partial))
            if len(ingest) >= MAX_INGEST:
                dropped.append(b)
            else:
                ingest.append(b)
                t += ingest_s          # persist the frame at ingest (HYBRID): main-loop time, measured 0.41 s
        if nxt > k0:
            drains.append((t, nxt - k0))

    def n_of(b, mod):
        c = content[b.frame]
        if mod in ("demosaic", "tile"):
            return 1
        if mod == "cls":
            return 99
        if mod == "seg":
            return int(round(c["nseg"][b.eff["cls"]]))
        ns = int(round(c["nseg"][b.eff["cls"]]))
        return int(round(ns * c["nenc_ratio"][b.eff["seg"]]))

    seen = []                                     # frames whose tile counts are known (past segmentation)

    def low_service(b):
        """Predicted per-frame service at lowest effort, from the frame's tile counts."""
        c = content[b.frame]
        ns = int(round(c["nseg"]["low"]))
        ne = int(round(ns * c["nenc_ratio"]["low"]))
        return (cost.lat(1, 1) + cost.lat(2, 1) + cost.lat(SLOT["cls"]["low"], 99)
                + cost.lat(SLOT["seg"]["low"], ns) + cost.lat(SLOT["enc"]["low"], ne) + over + ingest_s)

    def rho_low():
        """Arrival rate over the last rho_window admitted frames (window closed at now,
        so it decays in gaps) x mean predicted lowest-effort service of the last
        rho_window frames whose tile counts are known."""
        arr = [x.arr for x in batches[:nxt]][-(rho_window + 1):]
        if len(arr) < 2 or not seen:
            return 0.0
        if rho_rate == "drain":
            # what the daemon can see: per-frame cadence of each drain interval
            # (time since the previous drain / frames found), median of the last
            # rho_window frames' samples
            samp = []
            for (t0, _), (t1, k) in zip(drains, drains[1:]):
                samp += [(t1 - t0) / k] * k
            samp = samp[-rho_window:]
            if not samp:
                return 0.0
            lam = 1.0 / max(statistics.median(samp), 1e-9)
        elif rho_rate == "median":
            # cadence: robust to the gaps between acquisition stretches
            lam = 1.0 / max(statistics.median(b - a for a, b in zip(arr, arr[1:])), 1e-9)
        else:
            lam = len(arr) / max(t - arr[0], 1e-9)
        svc = statistics.mean(low_service(x) for x in seen[-rho_window:])
        return lam * svc

    def svc_est(e):
        """Per-frame service at effort e (all multi-effort stages at e) for the
        median of the last rho_window frames with known tile counts, + overheads."""
        if not seen:
            return None
        vals = []
        for x in seen[-rho_window:]:
            c = content[x.frame]
            ns = int(round(c["nseg"][e])); ne = int(round(ns * c["nenc_ratio"][e]))
            vals.append(cost.lat(1, 1) + cost.lat(2, 1) + cost.lat(SLOT["cls"][e], 99)
                        + cost.lat(SLOT["seg"][e], ns) + cost.lat(SLOT["enc"][e], ne) + over + ingest_s)
        return statistics.median(vals)

    def edf_tier(now, head):
        """Highest tier for the head such that every queued frame still meets
        its deadline with the head at that effort and the rest at lowest
        (EDF order, service estimated from recent frames)."""
        lo = svc_est("low")
        if lo is None:
            return "best"
        q = sorted(ingest + partial, key=lambda b: b.dl)
        frac = lambda b: (len(MODULES) - 1 - b.prog) / len(MODULES)   # work left in a started batch
        for e, mode in (("high", "best"), ("med", "capmed")):
            se = svc_est(e)
            t = now
            ok = True
            for b in q:
                t += (se if b is head else lo) * frac(b) * lat_scale
                if t > b.dl + edf_margin:
                    ok = False
                    break
            if ok:
                return mode
        return "lowest"

    def lat(b, mod, e):
        if mod == "demosaic":
            return cost.lat(1, 1) * lat_scale
        if mod == "tile":
            return cost.lat(2, 1) * lat_scale
        saved = b.eff.get(mod)
        b.eff[mod] = e
        v = cost.lat(SLOT[mod][e], n_of(b, mod)) * lat_scale
        if saved is None:
            del b.eff[mod]
        else:
            b.eff[mod] = saved
        return v

    def decide(b, mod, mode, now):
        """Effort for module mod, or None (NOT_FOUND)."""
        if mod in ("demosaic", "tile"):
            return "default"
        if policy.startswith("static-"):
            return policy.split("-")[1]
        left = len(MODULES) - MODULES.index(mod)
        budget = (b.dl - now) / left
        if mode == "lowest":
            return "low"
        order = [("high", None)]
        if mode == "capmed":
            order = []
        order += [("med", gmed if mode != "capmed" else float("inf")), ("low", glow if mode != "capmed" else float("inf"))]
        for e, gate in order:
            if gate is not None and not budget < gate:
                continue
            if e == "low":
                return "low"                      # lowest level: latency bypass
            if lat(b, mod, e) <= budget:
                return e
        return None

    # stretch id per frame from the trace: a new stretch after a gap > 30 s
    # (what the uplinked acquisition plan states exactly)
    stretch, sid = [], 0
    for i, b_ in enumerate(batches):
        if i and b_.arr - batches[i - 1].arr > 30.0:
            sid += 1
        stretch.append(sid)

    def plan_guard(d):
        """Lowest if, at lowest effort, the backlog projected to the end of the
        current stretch reaches SLO_routine / k (frames still to come from the plan)."""
        lo = svc_est("low")
        if lo is None or nxt == 0:
            return False
        cur = stretch[nxt - 1]
        n_rem = sum(1 for j in range(nxt, len(batches)) if stretch[j] == cur)
        arr = [x.arr for x in batches[:nxt]][-(rho_window + 1):]
        if len(arr) < 2:
            return False
        cad = statistics.median(b2 - a for a, b2 in zip(arr, arr[1:]))
        growth = max(lo - cad, 0.0)
        return d * lo + n_rem * growth >= slo_routine / k_rule

    def hist_guard(d):
        """Lowest iff the overload (rho_low >= 1) would push the lowest-effort
        backlog past SLO_routine/k before the current stretch is expected to
        end; expected end = longest stretch observed so far (from arrival gaps
        > 30 s), or never (= rho guard) before one has been observed."""
        lo = svc_est("low")
        arr = [x.arr for x in batches[:nxt]]
        if lo is None or len(arr) < 2:
            return False
        recent = arr[-(rho_window + 1):]
        cad = statistics.median(b2 - a for a, b2 in zip(recent, recent[1:]))
        rho = lo / cad
        if rho < 1.0:
            return False
        # episode boundary: a gap the system can catch up in -- longer than the
        # lowest-effort service time (no constant), or a fixed gap for comparison
        if hist_gap == "backlog":
            # an episode ends at a gap long enough to drain the backlog the
            # previous frame found (at lowest effort): no constant
            starts = [0] + [i for i in range(1, len(arr))
                            if arr[i] - arr[i - 1] > (depth_at_arrival[i - 1] + 1) * lo]
        elif hist_gap == "busy":
            # busy periods: an episode starts with a frame that finds the system empty
            starts = [i for i in range(len(arr)) if i == 0 or idle_arrival[i]]
        else:
            gap = lo if hist_gap is None else hist_gap
            starts = [0] + [i for i in range(1, len(arr)) if arr[i] - arr[i - 1] > gap]
        cur_start = arr[starts[-1]]
        done = [arr[e - 1] - arr[s_] for s_, e in zip(starts, starts[1:])
                if hist_window is None or t - arr[e - 1] <= hist_window]
        if not done:
            return True
        t_rem = max(max(done) - (t - cur_start), 0.0)
        w = d * lo
        t_v = (slo_routine / k_rule - w) / (rho - 1.0) if rho > 1.0 else float("inf")
        return t_v < t_rem

    def mode_now():
        d = len(ingest) + len(partial)
        if policy == "ladder-hist" and hist_guard(d):
            return "lowest"
        if policy == "ladder-plan" and plan_guard(d):
            return "lowest"
        if policy == "ladder-rho" and rho_low() >= 1.0:
            return "lowest"
        if policy == "edf":
            return "best"
        if policy in ("ladder", "ladder-rho", "ladder-edf", "ladder-plan", "ladder-hist"):
            return "best" if d < q1 else ("capmed" if d < q2 else "lowest")
        return "best" if d < q else "lowest"

    def run_one(src, one_module):
        """1 = progress, 0 = empty, 2 = halted head made no progress."""
        nonlocal t, preempts, maxdepth
        if not src:
            return 0
        src.sort(key=lambda b: b.dl)
        b = src[0]
        mode = mode_now()
        if policy in ("ladder-edf", "edf"):
            rank = {"best": 0, "capmed": 1, "lowest": 2}
            m2 = edf_tier(t, b)
            mode = m2 if policy == "edf" else max(mode, m2, key=lambda m: rank[m])
        maxdepth = max(maxdepth, len(ingest) + len(partial))
        prog0 = b.prog
        first = True
        while b.prog + 1 < len(MODULES):
            mod = MODULES[b.prog + 1]
            e = decide(b, mod, mode, t)
            if e is None:
                b.halted += 1
                break
            if e != "default":
                b.eff[mod] = e
            t += lat(b, mod, e) + (over if b.prog == -1 else 0.0)
            b.prog += 1
            if mod == "seg":
                seen.append(b)
            first = False
            if one_module and b.prog + 1 < len(MODULES):
                # Stage boundary: yield only if a frame with an earlier
                # deadline is now waiting (otherwise no checkpoint is paid).
                admit()
                if any(o.dl < b.dl for o in ingest + partial if o is not b):
                    break
        if b.prog + 1 >= len(MODULES):
            b.done = t
            src.remove(b)
            return 1
        made = b.prog != prog0
        if src is partial:
            if one_module and made:
                t += ckpt; b.parked += 1
            return 1 if made else 2
        # from ingest: move to partial (or stay if partial full). As in the daemon,
        # both queues share MAX_QUEUE_SIZE (= MAX_INGEST); MAX_PARTIAL (10) only
        # limits parking by yield (the caller's one_module flag) and batch-mode drain.
        if len(partial) < MAX_INGEST:
            ingest.remove(b); partial.append(b)
            if one_module and made:
                t += ckpt; b.parked += 1; preempts += 1
        return 1 if made else 2

    while nxt < len(batches) or ingest or partial:
        admit()
        if not ingest and not partial:
            t = max(t, batches[nxt].arr)
            continue
        if gran == "stage":
            pool = ingest + partial
            pool.sort(key=lambda b: b.dl)
            head = pool[0]
            r = run_one(ingest if head in ingest else partial, len(partial) < MAX_PARTIAL or head in partial)
            if r != 1:
                other = partial if head in ingest else ingest
                r2 = run_one(other, len(partial) < MAX_PARTIAL) if other else 0
                if r2 != 1:
                    t = min(t + HALT_POLL, batches[nxt].arr) if nxt < len(batches) else t + HALT_POLL
            continue
        rp = run_one(partial, False)
        ri = 0 if rp == 1 else run_one(ingest, False)
        if rp != 1 and ri != 1:
            t = min(t + HALT_POLL, batches[nxt].arr) if nxt < len(batches) and batches[nxt].arr > t else t + HALT_POLL
            continue
        if rp == 1 and len(partial) < MAX_PARTIAL:
            admit()
            run_one(ingest, False)
    return batches, dropped, preempts, maxdepth


def quality(b):
    return statistics.mean(QUAL[m][b.eff[m]] for m in ("cls", "seg", "enc"))


def summarise(batches, dropped):
    out = {}
    for cls in ("urgent", "routine", "all"):
        bs = [b for b in batches if cls == "all" or b.cls == cls]
        if not bs:
            continue
        lat = sorted(b.done - b.arr for b in bs if b.done is not None)
        on = [b for b in bs if b.done is not None and b.done <= b.dl + 1e-9]
        p = lambda x: lat[min(len(lat) - 1, int(round(x * (len(lat) - 1))))] if lat else None
        out[cls] = {"n": len(bs), "hit": len(on) / len(bs), "p50": p(.5), "p99": p(.99), "max": lat[-1] if lat else None,
                    "U": sum(quality(b) for b in on) / len(bs),
                    "q_on": statistics.mean(quality(b) for b in on) if on else None}
    out["dropped"] = len(dropped)
    out["halts"] = sum(b.halted for b in batches)
    out["mix"] = {m: {e: sum(1 for b in batches if b.eff.get(m) == e) for e in EFF} for m in ("cls", "seg", "enc")}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trace", required=True)
    ap.add_argument("--content", required=True, help="matched | keepall | cloudy")
    ap.add_argument("--policy", required=True)
    ap.add_argument("--granularity", required=True, choices=("batch", "stage"))
    ap.add_argument("--batch-overhead-s", type=float, required=True)
    ap.add_argument("--ckpt-s", type=float, required=True)
    ap.add_argument("--q", type=float, default=5, help="queue-depth switch (shipped: 5)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    content, sets = build_content()
    frames = sets[a.content]
    if not frames:
        raise SystemExit(f"no frames for content {a.content}")
    cost = Cost(os.path.join(ROOT, "analysis/cost_model.json"))
    bs, dr, pre, md = simulate(read_trace(a.trace), frames, content, cost, a.policy, a.granularity,
                               a.batch_overhead_s, a.ckpt_s, q=a.q)
    s = summarise(bs, dr)
    s.update(preempts=pre, max_depth=md)
    print(json.dumps(s, indent=1) if a.json else s)


if __name__ == "__main__":
    sys.exit(main())
