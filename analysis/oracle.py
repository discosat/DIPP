#!/usr/bin/env python3
"""Dynamic oracle: the best per-frame effort combination (cls, seg, enc; 27 choices)
chosen jointly over all frames with full knowledge of the future, as an upper bound
for SIPP on the board-grid cells. Protocol: analysis/lab/oracle_protocol.md.

    ~/.venvs/dipp-oracle/bin/python analysis/oracle.py run -o analysis/lab --seeds 1 2 3
    ~/.venvs/dipp-oracle/bin/python analysis/oracle.py check

Upper bound by the preemptive relaxation of the simulator (one processor): per frame
choose an effort per stage (cls, seg, enc) or skip it; maximise the quality of on-time
frames subject to Horn's condition -- for every window [t1, D] (t1 a release, D a
deadline) the work of on-time frames released at or after t1 and due by D fits in
D - t1 -- with violated windows added lazily from preemptive EDF until the chosen set is
schedulable; E37 cells add cumulative energy above the floor by each deadline. Every
rounding is optimistic and every simulator restriction (non-preemption at module
level, EDF order, no skipping, queue cap, checkpoints) is relaxed, so the optimum is an
upper bound under the simulator's cost model. The choice is replayed through
policy_lab.simulate() (FixedAssignment) under exact semantics (a diagnostic only: the choices are
optimised for the relaxed problem, see oracle_protocol.md addendum 2026-11-03).
"""
import argparse, copy, csv, itertools, math, multiprocessing as mp, os, sys, time

import yaml
from ortools.sat.python import cp_model

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, HERE)
import policy_lab as L  # noqa: E402
from predict_board_grid import build_scenario  # noqa: E402

STAGES = ("cls", "seg", "enc")
EFF = ("low", "med", "high")
COMBOS = list(itertools.product(EFF, repeat=3))
W = {s: {e: round(L.QUAL[s][e] * 1000) for e in EFF} for s in STAGES}   # QUAL has 3 decimals
PRE_SPAN_MS = 120_000                                                   # windows added up front


def ms_down(x):
    return int(math.floor(x * 1000 + 1e-9))


def ms_up(x):
    return int(math.ceil(x * 1000 - 1e-9))


def frames_of(sc):
    """(arr_ms, dl_ms, class, n per stage) with optimistic rounding."""
    out = []
    for a, slo, c, ns, ne in sc["arrivals"]:
        out.append((ms_down(a), ms_up(a + slo), c, {"cls": 99, "seg": ns, "enc": ne}))
    return out


# Per-frame fixed time (s) replacing the simulator's ingest_s + over (--fixed-s). The
# simulator's 0.71 s exceeds what the board spends between back-to-back frames (median
# 0.37 s, minimum 0.13 s), so a bound using it can be below a board result.
FIXED_S = None


def costs(sc, n):
    """Durations (ms, rounded down) and module energies (mJ, rounded down) of one frame."""
    c = sc["cost"]
    over = FIXED_S if FIXED_S is not None else sc["ingest_s"] + sc["over"]
    fixed_d = ms_down(over + c.lat(1, 1) + c.lat(2, 1))
    fixed_e = int((c.en(1, 1) + c.en(2, 1)) * 3.6)                 # 1 uWh = 3.6 mJ
    d = {s: {e: ms_down(c.lat(L.SLOT[s][e], n[s])) for e in EFF} for s in STAGES}
    en = {s: {e: int(c.en(L.SLOT[s][e], n[s]) * 3.6) for e in EFF} for s in STAGES}
    return fixed_d, fixed_e, d, en


def edf_windows(jobs, limit=50):
    """Preemptive EDF on jobs [(r, d, p)] (ms). Returns violated windows (t1, D), at most
    limit: at each deadline miss the jobs with r >= t1 and d <= D need more than D - t1
    (Horn's condition; D the missed deadline, t1 a release time); the missing job is then
    dropped and EDF continues, to collect further windows. [] means all deadlines met."""
    import heapq
    js = sorted(jobs)
    dropped, out = set(), []
    t, i, heap = 0, 0, []
    while (i < len(js) or heap) and len(out) < limit:
        if not heap:
            t = max(t, js[i][0])
        while i < len(js) and js[i][0] <= t:
            r, d, p = js[i]
            heapq.heappush(heap, [d, p, r, i]); i += 1
        d, rem, r, k = heap[0]
        nxt = js[i][0] if i < len(js) else None
        if nxt is None or t + rem <= nxt:
            t += rem
            heapq.heappop(heap)
            if t > d + 1e-6:
                live = [x for j, x in enumerate(js) if j not in dropped]
                best, arg = 0, None
                for t1 in sorted({x[0] for x in live if x[0] < d}):
                    dem = sum(x[2] for x in live if x[0] >= t1 and x[1] <= d)
                    if dem - (d - t1) > best + 1e-6:
                        best, arg = dem - (d - t1), t1
                if arg is None:                         # only the first miss is guaranteed a window
                    assert out, "EDF missed a deadline without a violated window"
                else:
                    out.append((arg, d))
                dropped.add(k)
                t -= rem                                # the dropped job's work never ran
        else:
            heap[0][1] -= nxt - t
            t = nxt
    return out


def solve(sc, time_limit, workers, hint=None, fix=None, gap=1e-3):
    """Upper bound by the preemptive relaxation: choose per frame a combination or skip,
    maximising on-time quality subject to Horn's window conditions (added lazily from
    preemptive EDF) and, for E37 cells, cumulative energy by each deadline.
    Returns dict(status, obj_U, ub_U, assign {(i, stage): effort}, present, ontime, wall, iters)."""
    fr = frames_of(sc)
    N = len(fr)
    data = [costs(sc, n) for (_, _, _, n) in fr]
    m = cp_model.CpModel()
    P, X, dur, obj = [], [], [], []
    for i, ((a, dl, _, _), (fd, _, d, _)) in enumerate(zip(fr, data)):
        p = m.new_bool_var(f"p{i}")                     # frame on time (else skipped / late)
        xs = {s: {e: m.new_bool_var(f"x{i}{s}{e}") for e in EFF} for s in STAGES}
        for s in STAGES:
            m.add(sum(xs[s].values()) == p)
            if fix is not None:
                m.add(xs[s][fix[STAGES.index(s)]] == p)
        di = fd * p + sum(xs[s][e] * d[s][e] for s in STAGES for e in EFF)
        m.add(di <= dl - a)                             # each frame fits its own window
        obj += [xs[s][e] * W[s][e] for s in STAGES for e in EFF]
        P.append(p); X.append(xs); dur.append(di)

    E = sc.get("energy")
    if E:                                              # energy by each deadline (mJ above the floor)
        assert E["sunfrac"] == 1.0 and E["mode"] == "always", "energy model assumes continuous sun, board always on"
        net_w = E["p_sun"] - E["bus"] - E["p_idle"]
        assert net_w > 0, net_w
        l0 = int(math.ceil((E["w0"] - E["floor"]) * 3.6e6))
        order = sorted(range(N), key=lambda i: fr[i][1])
        acc = []
        for k, i in enumerate(order):
            fd, fe, d, en = data[i]
            acc.append(fe * P[i] + sum(X[i][s][e] * en[s][e] for s in STAGES for e in EFF))
            D = fr[i][1]
            if k + 1 == N or fr[order[k + 1]][1] != D:
                m.add(sum(acc) <= l0 + int(math.ceil(net_w * D)))     # W * ms = mJ
    # short windows up front (span <= PRE_SPAN_MS), one per distinct set of contained frames;
    # longer ones are added lazily below
    rel = sorted({f[0] for f in fr})
    dls = sorted({f[1] for f in fr})
    seen = set()
    for t1 in rel:
        for D in dls:
            if D <= t1 or D - t1 > PRE_SPAN_MS:
                continue
            inside = tuple(i for i in range(N) if fr[i][0] >= t1 and fr[i][1] <= D)
            if len(inside) < 2 or inside in seen:
                continue
            seen.add(inside)
            m.add(sum(dur[i] for i in inside) <= D - t1)
    m.maximize(sum(obj))

    sol = cp_model.CpSolver()
    sol.parameters.num_search_workers = workers
    sol.parameters.relative_gap_limit = gap
    t0, iters, cuts = time.time(), 0, set()
    prev, st = None, cp_model.UNKNOWN
    if hint:
        prev = {i: (h[0], h[1]) for i, h in hint.items() if all(s in h[0] for s in STAGES)}
    while True:
        iters += 1
        left = time_limit - (time.time() - t0)
        if left <= 1:
            break
        sol.parameters.max_time_in_seconds = left
        m.clear_hints()
        if prev:
            for i, (eff, on) in prev.items():
                m.add_hint(P[i], 1 if on else 0)
                for s in STAGES:
                    for e in EFF:
                        m.add_hint(X[i][s][e], 1 if (on and eff.get(s) == e) else 0)
        st = sol.solve(m)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
        chosen = [i for i in range(N) if sol.value(P[i])]
        jobs = [(fr[i][0], fr[i][1], sol.value(dur[i])) for i in chosen]
        ws = [w for w in edf_windows(jobs) if w not in cuts]
        eff_now = {i: ({s: next(e for e in EFF if sol.value(X[i][s][e])) for s in STAGES}, True)
                   for i in chosen}
        prev = {i: eff_now.get(i, ({}, False)) for i in range(N)}
        if not edf_windows(jobs, limit=1):
            break                                       # schedulable (optimal, or out of time)
        assert ws, "violated windows already added"
        for t1, D in ws:
            cuts.add((t1, D))
            m.add(sum(dur[i] for i in range(N) if fr[i][0] >= t1 and fr[i][1] <= D) <= D - t1)
    wall = time.time() - t0
    out = {"status": sol.status_name(st), "wall": round(wall, 1), "iters": iters, "cuts": len(cuts)}
    scale = 3000.0 * N
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        out.update(obj_U=float("nan"), ub_U=float("nan"), assign={}, present=[], ontime=[])
        return out
    feasible = not edf_windows([(fr[i][0], fr[i][1], sol.value(dur[i])) for i in range(N) if sol.value(P[i])], limit=1)
    out["converged"] = bool(feasible and st == cp_model.OPTIMAL)
    out["obj_U"] = sol.objective_value / scale if feasible else float("nan")
    out["ub_U"] = sol.best_objective_bound / scale     # bound of a relaxation of the relaxation: valid
    out["assign"] = {(i, s): next(e for e in EFF if sol.value(X[i][s][e])) if sol.value(P[i]) else "low"
                     for i in range(N) for s in STAGES}
    out["present"] = [bool(sol.value(p)) for p in P]
    out["ontime"] = out["present"]
    return out


def lp_bound(sc, time_limit=900):
    """Upper bound from the LP relaxation of the window model (GLOP): x, p in [0, 1],
    Horn's windows added lazily from preemptive EDF on the fractional work until none is
    violated, E37 energy by each deadline. Valid even where the integer model does not
    converge. Returns dict(ub_U, iters, cuts, wall, converged)."""
    from ortools.linear_solver import pywraplp
    fr = frames_of(sc)
    N = len(fr)
    data = [costs(sc, n) for (_, _, _, n) in fr]
    lp = pywraplp.Solver.CreateSolver("GLOP")
    P, X, dur, obj = [], [], [], 0
    for i, ((a, dl, _, _), (fd, _, d, _)) in enumerate(zip(fr, data)):
        p = lp.NumVar(0, 1, f"p{i}")
        xs = {s_: {e: lp.NumVar(0, 1, f"x{i}{s_}{e}") for e in EFF} for s_ in STAGES}
        for s_ in STAGES:
            lp.Add(sum(xs[s_].values()) == p)
        di = fd * p + sum(xs[s_][e] * d[s_][e] for s_ in STAGES for e in EFF)
        lp.Add(di <= dl - a)
        obj += sum(xs[s_][e] * W[s_][e] for s_ in STAGES for e in EFF)
        P.append(p); X.append(xs); dur.append(di)
    E = sc.get("energy")
    if E:
        net_w = E["p_sun"] - E["bus"] - E["p_idle"]
        assert E["sunfrac"] == 1.0 and E["mode"] == "always" and net_w > 0
        l0 = int(math.ceil((E["w0"] - E["floor"]) * 3.6e6))
        order = sorted(range(N), key=lambda i: fr[i][1])
        acc = 0
        for k, i in enumerate(order):
            fd, fe, d, en = data[i]
            acc += fe * P[i] + sum(X[i][s_][e] * en[s_][e] for s_ in STAGES for e in EFF)
            D = fr[i][1]
            if k + 1 == N or fr[order[k + 1]][1] != D:
                lp.Add(acc <= l0 + int(math.ceil(net_w * D)))
    lp.Maximize(obj)
    t0, iters, cuts, conv = time.time(), 0, set(), False
    while time.time() - t0 < time_limit:
        iters += 1
        st = lp.Solve()
        assert st == pywraplp.Solver.OPTIMAL, f"LP status {st}"
        last_obj = lp.Objective().Value()      # valid bound even if cuts follow
        work = [fd * P[i].solution_value() + sum(X[i][s_][e].solution_value() * data[i][2][s_][e]
                                                 for s_ in STAGES for e in EFF)
                for i, (fd, _, _, _) in enumerate(data)]
        jobs = [(fr[i][0], fr[i][1], work[i]) for i in range(N) if work[i] > 1e-6]
        ws = [w for w in edf_windows(jobs, limit=200) if w not in cuts]
        if not edf_windows(jobs, limit=1):
            conv = True
            break
        assert ws, "violated windows already added (LP)"
        for t1, D in ws:
            cuts.add((t1, D))
            lp.Add(sum(dur[i] for i in range(N) if fr[i][0] >= t1 and fr[i][1] <= D) <= D - t1)
    # the LP optimum with a subset of the windows bounds the full LP from above, so it is
    # an upper bound whether or not all violated windows were found
    return {"ub_U": last_obj / (3000.0 * N), "iters": iters, "cuts": len(cuts),
            "wall": round(time.time() - t0, 1), "converged": conv}


def one_lp(job):
    cell, sd, r, tl = job
    sc = build_scenario(r)[0]
    res = lp_bound(sc, tl)
    print(f"{cell} s{sd}: LP UB {res['ub_U']:.4f} conv {res['converged']} iters {res['iters']} "
          f"cuts {res['cuts']} [{res['wall']} s]", flush=True)
    return {"cell": cell, "seed": sd, "UB_LP": round(res["ub_U"], 4), "lp_converged": res["converged"],
            "lp_iters": res["iters"], "lp_cuts": res["cuts"], "lp_wall_s": res["wall"]}


def cmd_lp(a):
    jobs = [(cell, sd, r, a.time) for cell, sd, r in grid_runs(a.seeds) if not a.cells or cell in a.cells]
    with mp.Pool(a.procs) as pool:
        rows = pool.map(one_lp, jobs, chunksize=1)
    tag = f"_{a.tag}" if a.tag else ""
    write(os.path.join(a.o, f"oracle_lp{tag}.csv"), rows)
    print("wrote", a.o)


# ------------------------------------------------------------------ runs
def grid_runs(seeds):
    """One run dict per (board-grid cell, seed), from the cell's final-s1 entry."""
    runs = yaml.safe_load(open(os.path.join(ROOT, "experiments/matrices/board-grid.yaml")))["runs"]
    base = {}
    for r in runs:
        cell, rest = r["label"].split("-", 1)
        if rest == "final-s1":
            base[cell] = r
    out = []
    for cell in sorted(base):
        for sd in seeds:
            r = copy.deepcopy(base[cell])
            assert r["arrivals"].endswith("-s1.trace"), r["arrivals"]
            r["arrivals"] = r["arrivals"][: -len("-s1.trace")] + f"-s{sd}.trace"
            r["label"] = f"{cell}-final-s{sd}"
            out.append((cell, sd, r))
    return out


def sim(r, pol, record=False):
    sc = build_scenario(r)[0]
    if record:
        sc["record"] = []
    return L.simulate(sc, pol)


def sipp_policy():
    return [p for p in L.policy_set("final3") if p.name == "sipp"][0]


def one(job):
    cell, sd, r, tl, workers = job
    sc = build_scenario(r)[0]
    N = len(sc["arrivals"])
    # SIPP (simulated) and its choices as the solver hint
    s_out = sim(r, sipp_policy(), record=True)
    hint = {i: (dict(eff), done is not None and done <= dl + 1e-9)
            for i, (_, dl, done, _, eff) in enumerate(s_out["frames"])}
    res = solve(sc, tl, workers, hint=hint)
    rep = {}
    for g in ("stage", "batch"):
        rep[g] = sim(r, L.FixedAssignment(res["assign"], gran=g))["U"] if res["assign"] else float("nan")
    gbest = max(rep, key=lambda g: rep[g])
    statics = {}
    for c in COMBOS:
        a = {(i, s): c[k] for i in range(N) for k, s in enumerate(STAGES)}
        statics[c] = sim(r, L.FixedAssignment(a, gran="batch"))["U"]
    b27 = max(statics, key=lambda c: statics[c])
    buni = max(((e, e, e) for e in EFF), key=lambda c: statics[c])
    row = {"cell": cell, "seed": sd, "frames": N, "status": res["status"], "converged": res.get("converged"),
           "iters": res.get("iters"), "cuts": res.get("cuts"), "wall_s": res["wall"],
           "UB": round(res["ub_U"], 4), "U_oracle": round(res["obj_U"], 4),
           "U_replay": round(rep[gbest], 4), "replay_gran": gbest,
           "U_replay_stage": round(rep["stage"], 4), "U_replay_batch": round(rep["batch"], 4),
           "best27_static_U": round(statics[b27], 4), "best27_combo": "/".join(b27),
           "best_uniform_static_U": round(statics[buni], 4), "best_uniform": buni[0],
           "U_sipp": round(s_out["U"], 4),
           "sipp_pct_UB": round(100 * s_out["U"] / res["ub_U"], 1) if res["ub_U"] > 0 else "",
           "sipp_pct_replay": round(100 * s_out["U"] / rep[gbest], 1) if rep[gbest] > 0 else ""}
    combos = [{"cell": cell, "seed": sd, "frame": i, "class": sc["arrivals"][i][2],
               "present": int(res["present"][i]) if res["present"] else "",
               "on_time": int(res["ontime"][i]) if res["ontime"] else "",
               **{s: res["assign"].get((i, s), "") for s in STAGES}} for i in range(N)]
    allst = [{"cell": cell, "seed": sd, "combo": "/".join(c), "U": round(statics[c], 4)} for c in COMBOS]
    print(f"{cell} s{sd}: {res['status']} UB {row['UB']} oracle {row['U_oracle']} replay {row['U_replay']} "
          f"best27 {row['best27_static_U']} ({row['best27_combo']}) sipp {row['U_sipp']} [{res['wall']} s]",
          flush=True)
    return row, combos, allst


def write(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)


def cmd_run(a):
    jobs = [(cell, sd, r, a.time, a.workers) for cell, sd, r in grid_runs(a.seeds)
            if not a.cells or cell in a.cells]
    with mp.Pool(a.procs) as pool:
        out = pool.map(one, jobs, chunksize=1)
    os.makedirs(a.o, exist_ok=True)
    tag = f"_{a.tag}" if a.tag else ""
    write(os.path.join(a.o, f"oracle_results{tag}.csv"), [r for r, _, _ in out])
    write(os.path.join(a.o, f"oracle_combos{tag}.csv"), [c for _, cs, _ in out for c in cs])
    write(os.path.join(a.o, f"oracle_statics{tag}.csv"), [s for _, _, ss in out for s in ss])
    print("wrote", a.o)


def cmd_check(a):
    """Model sanity checks from the protocol (fail loudly)."""
    runs = {cell: r for cell, sd, r in grid_runs([1])}
    # 1) fixed all-high / all-low in the model vs simulated statics on P2C2
    r = runs["P2C2"]
    for e in ("high", "low"):
        sc = build_scenario(r)[0]
        res = solve(sc, a.time, a.workers, fix=(e, e, e))
        u_sim = sim(r, L.Static(e))["U"]
        print(f"P2C2 fixed {e}: model {res['obj_U']:.4f} (UB {res['ub_U']:.4f}, {res['status']}) sim {u_sim:.4f}")
        assert res["obj_U"] >= u_sim - 1e-6 and abs(res["obj_U"] - u_sim) <= 0.03, "fixed-combo model disagrees with simulator"
    # 2) toy instances: UB >= best simulated assignment over all 27^3 (3 frames); deadlines
    #    tightened to 8 s (and for E37 the start charge to 0.5 mWh above the floor) so that
    #    effort choice binds
    def toy(r):
        sc = build_scenario(r)[0]
        sc["arrivals"] = [(t, 8.0, c, ns, ne) for (t, _, c, ns, ne) in sc["arrivals"][:3]]
        if sc.get("energy"):
            sc["energy"]["w0"] = sc["energy"]["floor"] + 0.0005
        return sc
    for cell in ("P1C5", "E37P2C3"):
        r = runs[cell]
        sc = toy(r)
        res = solve(sc, a.time, a.workers)
        best = 0.0
        sc2 = toy(r)
        for cs in itertools.product(COMBOS, repeat=3):
            asg = {(i, s): cs[i][k] for i in range(3) for k, s in enumerate(STAGES)}
            for g in ("stage", "batch"):
                best = max(best, L.simulate(copy.deepcopy(sc2), L.FixedAssignment(asg, gran=g))["U"])
        print(f"{cell} toy (3 frames): UB {res['ub_U']:.4f} oracle {res['obj_U']:.4f} ({res['status']}) "
              f"best simulated assignment {best:.4f}")
        assert res["ub_U"] >= best - 1e-6, "oracle bound below a simulated assignment"
    print("checks OK")


SOFT_LEVELS = 8
SOFT_VARIANTS = (("G0.5", 0.5, False), ("G1", 1.0, False), ("G2", 2.0, False), ("G1uh", 1.0, True))


def soft_value(sc, frames, G, urgent_hard):
    """V_G of a simulated run (METRIC.md 2026-10-15): q * max(0, 1 - late / (G * D)),
    D the frame's relative deadline; urgent frames hard if urgent_hard; never done = 0."""
    v = 0.0
    for (arr, dl, done, cls, eff) in frames:
        if done is None or any(st not in eff for st in STAGES):
            continue
        late = max(0.0, done - dl)
        if late > 1e-9 and urgent_hard and cls == "urgent":
            continue
        g = max(0.0, 1.0 - late / (G * (dl - arr))) if late > 1e-9 else 1.0
        v += g * sum(L.QUAL[st][eff[st]] for st in STAGES) / 3.0
    return v / len(frames)


def lp_bound_soft(sc, G, urgent_hard, time_limit=1800):
    """Upper bound on V_G: as lp_bound, but a frame may complete at its deadline or at one
    of SOFT_LEVELS lateness levels d + l*G*D/SOFT_LEVELS, valued (optimistically) at
    g((l-1)/SOFT_LEVELS); urgent frames only at level 0 if urgent_hard."""
    from ortools.linear_solver import pywraplp
    fr = frames_of(sc)
    N = len(fr)
    data = [costs(sc, n) for (_, _, _, n) in fr]
    lp = pywraplp.Solver.CreateSolver("GLOP")
    jobs_meta, work, obj = [], [], 0          # per (frame, level): (release, deadline), work expr
    for i, ((a, dl, cls, _), (fd, fe, d, en)) in enumerate(zip(fr, data)):
        D = dl - a
        levels = [(dl, 1.0)]
        if not (urgent_hard and cls == "urgent"):
            levels += [(dl + int(math.ceil(l * G * D / SOFT_LEVELS)), 1.0 - (l - 1) / SOFT_LEVELS)
                       for l in range(1, SOFT_LEVELS + 1)]
        ps = []
        for li, (dd, gv) in enumerate(levels):
            p = lp.NumVar(0, 1, f"p{i}_{li}")
            xs = {s_: {e: lp.NumVar(0, 1, f"x{i}_{li}{s_}{e}") for e in EFF} for s_ in STAGES}
            for s_ in STAGES:
                lp.Add(sum(xs[s_].values()) == p)
            wk = fd * p + sum(xs[s_][e] * d[s_][e] for s_ in STAGES for e in EFF)
            lp.Add(wk <= dd - a)
            obj += gv * sum(xs[s_][e] * W[s_][e] for s_ in STAGES for e in EFF)
            en_k = fe * p + sum(xs[s_][e] * en[s_][e] for s_ in STAGES for e in EFF)
            jobs_meta.append((a, dd, en_k)); work.append(wk); ps.append(p)
        lp.Add(sum(ps) <= 1)
    E = sc.get("energy")
    if E:
        net_w = E["p_sun"] - E["bus"] - E["p_idle"]
        assert E["sunfrac"] == 1.0 and E["mode"] == "always" and net_w > 0
        l0 = int(math.ceil((E["w0"] - E["floor"]) * 3.6e6))
        order = sorted(range(len(jobs_meta)), key=lambda k: jobs_meta[k][1])
        acc = 0
        for n_, k in enumerate(order):
            acc += jobs_meta[k][2]
            D = jobs_meta[k][1]
            if n_ + 1 == len(order) or jobs_meta[order[n_ + 1]][1] != D:
                lp.Add(acc <= l0 + int(math.ceil(net_w * D)))
    lp.Maximize(obj)
    t0, iters, cuts, conv = time.time(), 0, set(), False
    while time.time() - t0 < time_limit:
        iters += 1
        st = lp.Solve()
        for retry in ("use_dual_simplex: true", "use_preprocessing: false"):
            if st == pywraplp.Solver.OPTIMAL:
                break
            lp.SetSolverSpecificParametersAsString(retry)     # numerical trouble (status 4): retry
            st = lp.Solve()
        assert st == pywraplp.Solver.OPTIMAL, f"LP status {st}"
        last_obj = lp.Objective().Value()      # valid bound even if cuts follow
        wv = [w.solution_value() for w in work]
        jobs = [(jobs_meta[k][0], jobs_meta[k][1], wv[k]) for k in range(len(work)) if wv[k] > 1e-6]
        ws = [w for w in edf_windows(jobs, limit=200) if w not in cuts]
        if not edf_windows(jobs, limit=1):
            conv = True
            break
        assert ws, "violated windows already added (soft LP)"
        for t1, D in ws:
            cuts.add((t1, D))
            lp.Add(sum(work[k] for k in range(len(work)) if jobs_meta[k][0] >= t1 and jobs_meta[k][1] <= D) <= D - t1)
    return {"ub": last_obj / (3000.0 * N), "iters": iters, "cuts": len(cuts),
            "wall": round(time.time() - t0, 1), "converged": conv}


def one_soft(job):
    cell, sd, r, tl, var = job
    name, G, uh = [v for v in SOFT_VARIANTS if v[0] == var][0]
    sc = build_scenario(r)[0]
    N = len(sc["arrivals"])
    res = lp_bound_soft(sc, G, uh, tl)
    sc2 = build_scenario(r)[0]; sc2["record"] = []
    s_out = L.simulate(sc2, sipp_policy())
    v_sipp = soft_value(sc2, s_out["frames"], G, uh)
    best, bcombo = -1.0, None
    for c in COMBOS:
        sc3 = build_scenario(r)[0]; sc3["record"] = []
        a_ = {(i, st): c[k] for i in range(N) for k, st in enumerate(STAGES)}
        o3 = L.simulate(sc3, L.FixedAssignment(a_, gran="batch"))
        v = soft_value(sc3, o3["frames"], G, uh)
        if v > best:
            best, bcombo = v, c
    print(f"{cell} s{sd} {name}: UB {res['ub']:.4f} (conv {res['converged']}, {res['wall']} s) "
          f"SIPP {v_sipp:.4f} best27 {best:.4f} {'/'.join(bcombo)}", flush=True)
    return {"cell": cell, "seed": sd, "variant": name, "UB_soft": round(res["ub"], 4),
            "converged": res["converged"], "wall_s": res["wall"], "V_sipp": round(v_sipp, 4),
            "U_sipp": round(s_out["U"], 4), "best27_V": round(best, 4), "best27_combo": "/".join(bcombo)}


def cmd_soft(a):
    jobs = [(cell, sd, r, a.time, v[0]) for cell, sd, r in grid_runs(a.seeds) for v in SOFT_VARIANTS
            if (not a.cells or cell in a.cells) and (not a.variants or v[0] in a.variants)]
    jobs.sort(key=lambda j: j[0] not in ("P1C5", "P1C4"))          # longest first
    with mp.Pool(a.procs) as pool:
        rows = pool.map(one_soft, jobs, chunksize=1)
    tag = f"_{a.tag}" if a.tag else ""
    write(os.path.join(a.o, f"oracle_soft{tag}.csv"), rows)
    print("wrote", a.o)


def cmd_summary(a):
    """Merge oracle_results.csv, oracle_lp.csv, oracle_combos.csv into oracle_summary.md."""
    import collections, statistics as st_
    rd = lambda f: list(csv.DictReader(open(os.path.join(a.o, f))))
    res = {(r["cell"], r["seed"]): r for r in rd("oracle_results.csv")}
    lpb = {(r["cell"], r["seed"]): r for r in rd("oracle_lp.csv")}
    fnum = lambda x: float(x) if x not in ("", "nan", None) and x == x else float("nan")
    rows, bad = [], []
    for k in sorted(res, key=lambda k: (k[0], int(k[1]))):
        r, l = res[k], lpb[k]
        ub_ip, ub_lp = fnum(r["UB"]), fnum(l["UB_LP"])
        ub = min(x for x in (ub_ip, ub_lp) if x == x)
        u_s, b27, buni, rep = fnum(r["U_sipp"]), fnum(r["best27_static_U"]), fnum(r["best_uniform_static_U"]), fnum(r["U_replay"])
        ach = max(x for x in (rep, b27) if x == x)
        for name, v in (("U_sipp", u_s), ("best27", b27), ("replay", rep)):
            if v == v and v > ub + 1e-3:
                bad.append(f"{k}: {name} {v} > UB {ub}")
        rows.append(dict(cell=k[0], seed=k[1], UB=ub, ub_src="IP" if ub_ip == ub_ip and ub_ip <= ub_lp + 1e-9 else "LP",
                         ip_status=r["status"], replay=rep, best27=b27, combo27=r["best27_combo"], buni=buni,
                         uni=r["best_uniform"], sipp=u_s, ach=ach))
    cells = collections.OrderedDict()
    for x in rows:
        cells.setdefault(x["cell"], []).append(x)
    mean = lambda xs: st_.mean(xs)
    L_ = ["# Dynamic oracle — results (simulation, board-grid cells, seeds 1-3)", "",
          "Protocol and addenda: oracle_protocol.md. Generated by `analysis/oracle.py summary`.", "",
          "UB = min(integer best bound, LP bound): upper bound on U under the simulator's cost model with",
          "preemption anywhere, free order, skipping of frames, no queue cap, energy checked at deadlines.",
          "The true optimum under the simulator's rules lies between best27 (a reachable schedule) and UB.",
          "The oracle's per-frame choices replayed through the simulator (U_replay in oracle_results.csv) are",
          "not a reference: they are optimised for the relaxed problem and lose 0.01-0.18 to it under the real",
          "rules, falling below SIPP in the heaviest cells (protocol addendum 2026-11-03).",
          "Best27 = best single (cls/seg/enc) combination for all frames, in hindsight; uniform = best of",
          "low/med/high for all stages (the board grid's best static). Means over 3 seeds.", "",
          "| cell | UB | best27 (combo) | best uniform | SIPP | SIPP % UB | SIPP − best27 | SIPP − uniform |",
          "|---|---|---|---|---|---|---|---|"]
    for c, xs in cells.items():
        ub, b27, bu, sp = (mean([x[k] for x in xs]) for k in ("UB", "best27", "buni", "sipp"))
        combos = collections.Counter(x["combo27"] for x in xs).most_common(1)[0][0]
        L_.append(f"| {c} | {ub:.3f} | {b27:.3f} ({combos}) | {bu:.3f} ({xs[0]['uni']}) | {sp:.3f} | "
                  f"{100 * sp / ub:.1f} | {sp - b27:+.3f} | {sp - bu:+.3f} |")
    # combination histogram of the oracle's on-time frames (converged integer solutions only)
    cmb = rd("oracle_combos.csv")
    conv = {(r["cell"], r["seed"]) for r in rd("oracle_results.csv") if r["status"] == "OPTIMAL"}
    L_ += ["", "## Effort chosen by the oracle (on-time frames, cells with an OPTIMAL integer solution)", "",
           "| cell | frames | cls low/med/high % | seg low/med/high % | enc low/med/high % |", "|---|---|---|---|---|"]
    by = collections.defaultdict(lambda: {s_: collections.Counter() for s_ in STAGES})
    nf = collections.Counter()
    for r in cmb:
        if (r["cell"], r["seed"]) in conv and r["on_time"] == "1":
            nf[r["cell"]] += 1
            for s_ in STAGES:
                by[r["cell"]][s_][r[s_]] += 1
    for c in cells:
        if not nf[c]:
            L_.append(f"| {c} | – | (no OPTIMAL integer solution) | | |")
            continue
        pc = lambda s_: "/".join(f"{100 * by[c][s_][e] / nf[c]:.0f}" for e in EFF)
        L_.append(f"| {c} | {nf[c]} | {pc('cls')} | {pc('seg')} | {pc('enc')} |")
    L_ += ["", "## Sanity", ""]
    L_ += [f"- VIOLATION {b}" for b in bad] or ["- UB ≥ SIPP, best27 and replay in every (cell, seed)."]
    open(os.path.join(a.o, "oracle_summary.md"), "w").write("\n".join(L_) + "\n")
    print("\n".join(L_))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--fixed-s", type=float, default=None, help="per-frame fixed time, s (default: the simulator's)")
    p.add_argument("-o", required=True)
    p.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    p.add_argument("--cells", nargs="*", default=[])
    p.add_argument("--time", type=float, default=600.0)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--procs", type=int, default=2)
    p.add_argument("--tag", default="")
    sf = sub.add_parser("soft")
    sf.add_argument("-o", required=True)
    sf.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    sf.add_argument("--cells", nargs="*", default=[])
    sf.add_argument("--variants", nargs="*", default=[])
    sf.add_argument("--time", type=float, default=1800.0)
    sf.add_argument("--procs", type=int, default=7)
    sf.add_argument("--tag", default="")
    q = sub.add_parser("lp")
    q.add_argument("--fixed-s", type=float, default=None, help="per-frame fixed time, s (default: the simulator's)")
    q.add_argument("-o", required=True)
    q.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    q.add_argument("--cells", nargs="*", default=[])
    q.add_argument("--time", type=float, default=900.0)
    q.add_argument("--procs", type=int, default=7)
    q.add_argument("--tag", default="")
    m_ = sub.add_parser("summary")
    m_.add_argument("-o", required=True)
    c = sub.add_parser("check")
    c.add_argument("--time", type=float, default=120.0)
    c.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    global FIXED_S
    FIXED_S = getattr(a, "fixed_s", None)
    {"run": cmd_run, "lp": cmd_lp, "soft": cmd_soft, "summary": cmd_summary, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    main()
