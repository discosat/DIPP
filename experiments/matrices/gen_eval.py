#!/usr/bin/env python3
"""Write the resubmission's evaluation matrix (2026-10-07) from the traces'
own headers.  python3 experiments/matrices/gen_eval.py  -> eval.yaml

Metric, thresholds and SLOs: analysis/stage5/METRIC.md (fixed before any run).
Content is the primary axis (cloud cover is unknown at planning time), load
the secondary (frame overlap is planned):

  cell   load  content                                  why
  C1     1x    natural CloudSEN12 test frames (cloudy)  typical pass: high fits
  C2     1x    matched (Earth-average mix)              high just fits
  C3     1x    clear-sky (every tile kept)              high overloads: best static changes
  C4     2x    matched                                  medium is best static
  C5     2x    clear-sky                                low is best static

Policies: static low / medium / high (pipelines 2 / 6 / 3), adaptive (pipeline
1, ladder Q 12/20, stage granularity), deadline-greedy (pipeline 1, Q inf/inf).
Ablations, single seed: adaptive at batch granularity, the original two-tier
policy (Q 5/5, batch), threshold sensitivity k = 1.5 (16/27) and k = 3 (8/13).
Seeds = the trace's target scatter (urgent placement); 3 seeds where the
headline comparison is made, 1 elsewhere. Runs are listed in priority order.
"""
import os, sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_arrival_trace as mat  # noqa: E402

TR = "experiments/traces"
CONTENT = {"cloudy": "cs12_test_[0-9]", "matched": "cs12_test_matched_", "clear": "cs12_test_matchedkeep_"}
POL = {
    "low": dict(pipeline=2), "med": dict(pipeline=6), "high": dict(pipeline=3),
    # default policy v2 (paper-rev/policy_v2_spec.md): COMMON as is
    "adaptive": dict(pipeline=1),
    "adaptive-noguard": dict(pipeline=1, sched_guard="off"),
    "adaptive-batch": dict(pipeline=1, sched_granularity="batch"),
    "adaptive-k1.5": dict(pipeline=1, sched_k_x10=15),
    "adaptive-k3": dict(pipeline=1, sched_k_x10=30),
    # baselines and the policy as evaluated on 2026-10-07 (v1)
    "greedy": dict(pipeline=1, sched_q_med="inf", sched_q_low="inf", sched_guard="off", sched_accept="split"),
    "twotier-orig": dict(pipeline=1, sched_q_med=5, sched_q_low=5, sched_granularity="batch", sched_guard="off",
                         sched_accept="split"),
    "adaptive-v1": dict(pipeline=1, sched_q_med=12, sched_q_low=20, sched_guard="off", sched_accept="split"),
}
COMMON = {
    "storage": "HYBRID", "heuristic": "BEST_EFFORT", "load": "trace", "images": 1, "cost_seed": "cost.seed",
    "min_drain": 150, "idle_window": 5, "ff_chunk": 60, "ff_tick": 0.5, "tail_ff": 5726,
    "platform": "disco2-bol.env",
    # policy v2 defaults, every one explicit
    "sched_q_med": "auto", "sched_q_low": "auto", "sched_granularity": "stage",
    "sched_gate_med_ms": 5000, "sched_gate_low_ms": 3000,
    "sched_window": 8, "sched_frame_overhead_ms": 780, "sched_guard": "on", "sched_accept": "feas",
    "sched_k_x10": 20, "sched_smax_high_ms": 12150, "sched_smax_med_ms": 7330,
}


def header(trace, key):
    for ln in open(os.path.join(ROOT, trace)):
        if ln.startswith(f"# {key}="):
            return float(ln.split("=", 1)[1])
    raise SystemExit(f"{trace}: no '# {key}=' header")


def steady(platform, phase0):
    env = mat.read_env(os.path.join(ROOT, "experiments/platforms", platform))
    prof = mat.read_profile(os.path.join(ROOT, "analysis/power-profile", env["DIPP_BATT_PROFILE"]))
    return round(mat.steady_soc(env, prof, phase0), 4)


def run(cell, load, content, policy, seed):
    trace = f"{TR}/{'main' if load == '1x' else 'main2x'}-s{seed}.trace"
    ph = header(trace, "phase0")
    r = {"label": f"{cell}-{policy}-s{seed}", "arrivals": trace, "image_prefix": CONTENT[content],
         "phase0": round(ph, 4), "soc0": steady("disco2-bol.env", ph)}
    r.update(POL[policy])
    return r


CELLS = {"C1": ("1x", "cloudy"), "C2": ("1x", "matched"), "C3": ("1x", "clear"),
         "C4": ("2x", "matched"), "C5": ("2x", "clear")}
BASE = ("low", "med", "high", "adaptive", "greedy")
# extra seeds: adaptive + the cell's best static (host simulator) + the static it is most likely confused with
SEEDED = {"C2": ("adaptive", "high", "med"), "C3": ("adaptive", "med"), "C4": ("adaptive", "med"),
          "C5": ("adaptive", "low")}


def main():
    runs = []
    # 1. every policy once in every cell (seed 1): the headline grid
    for c, (ld, ct) in CELLS.items():
        runs += [run(c, ld, ct, p, 1) for p in BASE]
    # 2. seeds 2-3 where the comparison is made
    for c, pols in SEEDED.items():
        ld, ct = CELLS[c]
        runs += [run(c, ld, ct, p, s) for s in (2, 3) for p in pols]
    # 3. ablations, seed 1
    runs += [run("C3", "1x", "clear", "adaptive-batch", s) for s in (1, 2, 3)]
    runs += [run(c, *CELLS[c], "twotier-orig", 1) for c in ("C2", "C3")]
    runs += [run(c, *CELLS[c], p, 1) for c in ("C3", "C4") for p in ("adaptive-k1.5", "adaptive-k3")]
    labels = [r["label"] for r in runs]
    assert len(labels) == len(set(labels)), "duplicate labels"
    # eval.yaml is the matrix AS RUN on 2026-10-07..09 (policy v1, before the
    # feeder fix); it is not regenerated. The list above is its run list, from
    # which eval2 is derived.

    # Re-run matrix (2026-10-09). Two reasons, both disclosed in METRIC.md:
    #  (a) bench fault: the trace feeder waited for the daemon to go idle
    #      past the next frame's due time and then fed every overdue frame at
    #      once (feed_trace.sh, fixed); every run whose feed.log has a frame
    #      > 1 s late is invalid and redone;
    #  (b) policy change: the utilisation guard is part of the default policy
    #      now, so every run of the default policy and its ablations is redone.
    # Static runs not in (a) and greedy / twotier runs not in (a) are kept.
    late = {"C5-high-s1", "C5-med-s1", "C5-low-s1", "C5-low-s2", "C5-low-s3", "C4-high-s1", "C3-high-s1",
            "C5-greedy-s1", "C4-greedy-s1", "C3-greedy-s1"}
    redo = [r for r in runs if r["label"] in late or
            r["label"].split("-s")[0].split("-", 1)[1] in ("adaptive", "adaptive-batch", "adaptive-k1.5", "adaptive-k3")]
    redo += [run("C5", "2x", "clear", "adaptive-noguard", s_) for s_ in (1, 2, 3)]
    # the policy as first evaluated (v1), with the fixed feeder, where it differed
    redo += [run(c, *CELLS[c], "adaptive-v1", 1) for c in ("C3", "C5")]
    # validation first: the two runs that tell whether the fix and the feeder fix hold
    first = ["C5-low-s1", "C5-adaptive-s1"]
    redo.sort(key=lambda r: (r["label"] not in first, first.index(r["label"]) if r["label"] in first else 0))
    m = {"run_set": "eval2", "defaults": dict(COMMON), "runs": redo}
    p = os.path.join(ROOT, "experiments/matrices/eval2.yaml")
    with open(p, "w") as fh:
        fh.write("# GENERATED by experiments/matrices/gen_eval.py -- do not edit; re-run it.\n"
                 "# Re-runs after the feeder fix (2026-10-09) with policy v2 (2026-10-11): every\n"
                 "# default-policy run and ablation, the feeder-affected statics/baselines, the\n"
                 "# guard ablation (C5) and v1 with the fixed feeder (C3, C5).\n")
        yaml.safe_dump(m, fh, sort_keys=False, width=200)
    print(f"eval2: {len(redo)} runs -> {p}")

    # Extra matrix, run after eval (its seed files are pushed separately):
    #  C6  interlock recovery: end of life, start 1 Wh below the 2-orbit floor
    #      (as after a safe-mode recovery), main-s1 x matched; the halt length is
    #      the same for every policy, the backlog it leaves is drained differently.
    #  C7  cost-model error (R3.3): C2 with every latency and energy in the seed
    #      x0.5 / x2 (mean power, hence the battery debit, unchanged).
    env = mat.read_env(os.path.join(ROOT, "experiments/platforms/disco2-eol.env"))
    floor = float(env["DIPP_BATT_SURVIVAL_ORBITS"]) * float(env["DIPP_BATT_BUS_W"]) * \
        float(env["DIPP_BATT_ORBIT_MIN"]) / 60
    soc_low = round((floor - 1.0) / float(env["DIPP_BATT_CAPACITY_WH"]), 4)
    extra = []
    for p_ in ("low", "med", "high", "adaptive"):
        r = run("C6", "1x", "matched", p_, 1)
        r.update(platform="disco2-eol.env", soc0=soc_low)
        extra.append(r)
    for f in ("0.5", "2"):
        r = run("C7", "1x", "matched", "adaptive", 1)
        r["label"] = f"C7-adaptive_cost_x{f}-s1"
        r["cost_seed"] = f"cost_x{f}.seed"
        extra.append(r)
    m = {"run_set": "eval-extra", "defaults": dict(COMMON), "runs": extra}
    p = os.path.join(ROOT, "experiments/matrices/eval-extra.yaml")
    with open(p, "w") as fh:
        fh.write("# GENERATED by experiments/matrices/gen_eval.py -- do not edit; re-run it.\n"
                 f"# C6 soc0 = (floor {floor:.2f} Wh - 1 Wh) / capacity. C7 needs cost_x0.5.seed and\n"
                 "# cost_x2.seed on the board (staging/eval-extra/, pushed after eval).\n")
        yaml.safe_dump(m, fh, sort_keys=False, width=200)
    print(f"eval-extra: {len(extra)} runs -> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
