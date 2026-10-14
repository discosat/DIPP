#!/usr/bin/env python3
"""Policy laboratory: the board model of tools/sched_sim.py with pluggable
tier policies and a synthetic scenario generator, for searching for a
default scheduling policy that holds across deadlines, burst shapes,
cadences and content without looking at the acquisition plan.

    python3 tools/policy_lab.py selftest          # matches sched_sim on shared policies
    python3 tools/policy_lab.py sweep -n 3000 -o analysis/lab/sweep1.csv

Board model (identical to sched_sim.simulate, which reproduces the board in
C1-C4 within 0.03 once calibrated): EDF over both queues; stage granularity
with yield to strictly earlier deadlines (checkpoint ckpt_s); per-module
efforts chosen by the gated per-module budget rule; per-batch overhead
over_s and per-ingested-frame cost ingest_s on the main loop; queue cap 100 for
both queues (MAX_QUEUE_SIZE), parking only while partial < 10; energy not modelled (never binds with the board powered per
burst).

A policy only chooses the TIER before each batch: "best" (high first,
gated medium/low), "capmed" (medium then low), "lowest". Everything a policy
uses is what the daemon can observe: queue depth, drain times, the cost
model at the input sizes past frames had.
"""
import argparse, bisect, collections, csv, math, os, random, statistics, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import sched_sim as S  # noqa: E402

EFF = ("low", "med", "high")
MODULES = S.MODULES
SLOT = S.SLOT
QUAL = S.QUAL
MAX_INGEST, MAX_PARTIAL, HALT_POLL = S.MAX_INGEST, S.MAX_PARTIAL, S.HALT_POLL
INF = float("inf")


# ------------------------------------------------------------------ cost
class Cost:
    """sched_sim.Cost with the high rung's latency scaled (a larger top model)."""

    def __init__(self, base, hi_scale=1.0):
        self.b, self.hi = base, hi_scale
        self.cache = {}

    def lat(self, slot, n):
        k = (slot, n)
        v = self.cache.get(k)
        if v is None:
            v = self.b.lat(slot, n)
            if slot in (SLOT["cls"]["high"], SLOT["seg"]["high"], SLOT["enc"]["high"]):
                v *= self.hi
            self.cache[k] = v
        return v

    def en(self, slot, n):
        """Module energy above board idle (uWh) at input size n: measured point, else fitted line."""
        k = ("e", slot, n)
        v = self.cache.get(k)
        if v is None:
            e = self.b.s[str(slot)]
            pts = {p_["n"]: p_["en_uwh"] for p_ in e["points"]}
            if n in pts:
                v = pts[n]
            else:
                m = e["model"]
                v = max(m["en_ref_uwh"] + m["en_slope_uwh"] * (n - e["n_ref"]), 0.0)
            if slot in (SLOT["cls"]["high"], SLOT["seg"]["high"], SLOT["enc"]["high"]):
                v *= self.hi
            self.cache[k] = v
        return v

    def frame_en(self, e, nseg, nenc):
        """Per-frame module energy at effort e (uWh, above idle)."""
        return (self.en(1, 1) + self.en(2, 1) + self.en(SLOT["cls"][e], 99)
                + self.en(SLOT["seg"][e], nseg) + self.en(SLOT["enc"][e], nenc))

    def frame(self, e, nseg, nenc, over):
        """Per-frame module time at effort e (all multi-effort stages at e) + over."""
        return (self.lat(1, 1) + self.lat(2, 1) + self.lat(SLOT["cls"][e], 99)
                + self.lat(SLOT["seg"][e], nseg) + self.lat(SLOT["enc"][e], nenc) + over)


class ModelCost(Cost):
    """What the policy believes: the true cost times a per-configuration error
    (a mis-seeded or drifted cost model). The board corrects latency on line,
    so this is the pessimistic case of a model that stays wrong."""

    def __init__(self, true, err):
        self.t, self.err = true, err

    def lat(self, slot, n):
        return self.t.lat(slot, n) * self.err.get(slot, 1.0)

    def en(self, slot, n):
        return self.t.en(slot, n) * self.err.get(slot, 1.0)


class Battery:
    """Generic platform energy model (no specific mission profile): square-wave
    generation with a sunlit fraction, constant bus load, the payload board's
    idle power while on (per burst: while busy and for hold_s after the last
    activity; or always), module energy debited on completion. Wh throughout."""

    def __init__(self, cap, floor, w0, bus, p_sun, sunfrac, t_orb, phase0, p_idle, hold, mode):
        self.cap, self.floor, self.W = cap, floor, w0
        self.bus, self.p_sun, self.sf, self.T, self.ph = bus, p_sun, sunfrac, t_orb, phase0
        self.p_idle, self.hold, self.mode = p_idle, hold, mode
        self.last_active = -1e18
        self.payload_wh = 0.0                  # cumulative payload energy (board idle + modules)
        self.hist = collections.deque()        # (t, W, payload_wh), one entry per advance
        self.min_W = w0

    def _sunlit(self, t0, t1):
        """Seconds of sunlight in [t0, t1]."""
        if t1 <= t0:
            return 0.0
        T, L = self.T, self.sf * self.T
        def cum(t):                            # sunlit seconds in [0, t] from the cycle start
            u = t + self.ph * T
            k, r = divmod(u, T)
            return k * L + min(r, L)
        return cum(t1) - cum(t0)

    def next_boundary(self, t):
        T, L = self.T, self.sf * self.T
        r = (t + self.ph * T) % T
        return t + (L - r if r < L else T - r) + 1e-6

    def sunlit_now(self, t):
        return (t + self.ph * self.T) % self.T < self.sf * self.T

    def board_on_seconds(self, t0, t1, busy):
        if self.W <= self.floor and not busy:
            return 0.0                         # at the floor the platform sheds the payload (ECSS 5.7.4b)
        if self.mode == "always" or busy:
            return t1 - t0
        return max(0.0, min(t1, self.last_active + self.hold) - t0)

    def advance(self, t0, t1, busy):
        if t1 <= t0:
            return
        on = self.board_on_seconds(t0, t1, busy)
        gen = self._sunlit(t0, t1) * self.p_sun
        payload = on * self.p_idle
        self.W = min(self.cap, max(0.0, self.W + (gen - self.bus * (t1 - t0) - payload) / 3600.0))
        self.payload_wh += payload / 3600.0
        if busy:
            self.last_active = t1
        self.min_W = min(self.min_W, self.W)
        self.hist.append((t1, self.W, self.payload_wh))
        while self.hist and t1 - self.hist[0][0] > 2 * self.T:
            self.hist.popleft()

    def debit(self, uwh):
        self.W = max(0.0, self.W - uwh / 1e6)
        self.payload_wh += uwh / 1e6
        self.min_W = min(self.min_W, self.W)

    def recharge_s(self, deficit_wh, p_s):
        """Seconds to gain deficit_wh while the payload waits, given the platform's
        available power p_s: the board keeps drawing its idle power while waiting when
        always on, and for the hold time when switched per burst."""
        if deficit_wh <= 0:
            return 0.0
        x = deficit_wh * 3600.0                          # J
        on = p_s - self.p_idle
        if self.mode == "always":
            return x / on if on > 0 else INF
        gain_hold = on * self.hold
        if on > 0 and x <= gain_hold:
            return x / on
        x -= max(gain_hold, 0.0)
        return self.hold + (x / p_s if p_s > 0 else INF)

    def full_recently(self, t):
        """The battery reached full charge within the last orbit: generation exceeded
        demand, and the trend no longer measures what is available."""
        return any(h[1] >= self.cap - 1e-3 for h in self.hist if t - h[0] <= self.T)

    def avail_power_sofar(self, t):
        """As avail_power; before one orbit of history the platform's stated orbit-average
        budget (generation - bus, an explicit platform input) if set, else the history so
        far (a partial orbit is biased by where in the orbit it lies)."""
        p = self.avail_power(t)
        if p is not None:
            return p
        if getattr(self, "p_budget", None) is not None:
            return self.p_budget
        if len(self.hist) < 2:
            return p
        old = self.hist[0]
        dt = t - old[0]
        return ((self.W - old[1]) + (self.payload_wh - old[2])) * 3600.0 / dt if dt > 0 else None

    def avail_power(self, t):
        """Learned platform power available to the payload (generation - bus), W,
        averaged over the last orbit from the battery trend; None before one orbit."""
        if not self.hist:
            return None
        old = None
        for h in self.hist:
            if t - h[0] >= self.T:
                old = h
            else:
                break
        if old is None:
            return None
        dt = t - old[0]
        return ((self.W - old[1]) + (self.payload_wh - old[2])) * 3600.0 / dt


# ------------------------------------------------------------------ frames
class Frame:
    __slots__ = ("i", "arr", "dl", "cls", "nseg", "nenc", "prog", "eff", "done", "parked")

    def __init__(self, i, arr, slo, cls, nseg, nenc):
        self.i, self.arr, self.dl, self.cls = i, arr, arr + slo, cls
        self.nseg, self.nenc = nseg, nenc            # tiles after classification / segmentation
        self.prog, self.eff, self.done, self.parked = -1, {}, None, 0


def quality(f):
    return (QUAL["cls"][f.eff["cls"]] + QUAL["seg"][f.eff["seg"]] + QUAL["enc"][f.eff["enc"]]) / 3.0


# ------------------------------------------------------------------ observer
class Obs:
    """What the daemon can observe, maintained incrementally."""

    def __init__(self, cost, over, ingest_s, window, boundary="slow"):
        self.cost, self.over, self.ingest_s, self.w = cost, over, ingest_s, window
        self.boundary = boundary        # "slow": gap > S_e; "backlog": gap > (depth + 1) * S_e
        self.svc = {e: collections.deque(maxlen=window) for e in EFF}   # per-frame service at e
        self.fen = {e: collections.deque(maxlen=window) for e in EFF}   # per-frame module energy at e (uWh)
        self.bat = None
        self.now = 0.0
        self.nseg = collections.deque(maxlen=window)                     # tiles after classification
        self.nenc = collections.deque(maxlen=window)                     # tiles after segmentation
        self.min_slo = INF                                               # tightest deadline seen at arrival
        self.win = collections.deque(maxlen=window)                      # recent frames' deadline windows d - a
        self.cad = collections.deque(maxlen=window)                     # per-frame drain cadence
        self.iat = collections.deque(maxlen=window)                     # inter-arrival from capture times
        self.cad_src = "drain"           # "drain" | "arrival" (capture time = deadline - class SLO)
        self.last_drain = None
        self.arrivals = 0
        self.last_arr = None
        self.ep = {e: {"start": None, "last": None, "done": []} for e in EFF}

    def note_drain(self, t, k):
        if self.last_drain is not None and t > self.last_drain:
            per = (t - self.last_drain) / k
            for _ in range(k):
                self.cad.append(per)
        self.last_drain = t

    def note_arrival(self, a, depth=0):
        """Overload episodes per effort e: runs of arrivals spaced at most S_e
        apart (a longer gap lets effort e catch up). Uses S_e as estimated when
        the gap is observed."""
        for e in EFF:
            s_e = self.s(e)
            ep = self.ep[e]
            if ep["last"] is None or s_e is None:
                if ep["last"] is None:
                    ep["start"] = a
            elif a - ep["last"] > (s_e if self.boundary == "slow" else (ep.get("depth", 0) + 1) * s_e):
                ep["done"].append((ep["last"], ep["last"] - ep["start"]))
                ep["start"] = a
            ep["last"] = a
            ep["depth"] = depth
        if self.last_arr is not None:
            self.iat.append(a - self.last_arr)
        self.last_arr = a

    def note_frame(self, f):
        self.nseg.append(f.nseg); self.nenc.append(f.nenc)
        o = self.over + self.ingest_s
        for e in EFF:
            self.svc[e].append(self.cost.frame(e, f.nseg, f.nenc, o))
            self.fen[e].append(self.cost.frame_en(e, f.nseg, f.nenc))

    def e_frame(self, e):
        d = self.fen[e]
        return statistics.median(d) if d else None

    def s(self, e):
        d = self.svc[e]
        return statistics.median(d) if d else None

    def c(self):
        src = self.iat if self.cad_src == "arrival" else self.cad
        return statistics.median(src) if src else None

    def rho(self, e):
        s, c = self.s(e), self.c()
        return None if s is None or not c else s / c

    def in_episode(self, t, e="low"):
        s_e = self.s(e)
        return self.last_arr is not None and s_e is not None and t - self.last_arr <= s_e

    def t_rem(self, t, e, prior, window_s=None):
        """Expected remaining time of the current overload episode at effort e."""
        ep = self.ep[e]
        tau = t - ep["start"] if ep["start"] is not None else 0.0
        done = [d for (end, d) in ep["done"] if window_s is None or t - end <= window_s]
        if prior == "inf":
            return INF
        if prior == "zero":
            return 0.0
        if prior == "lindy":
            return tau
        if prior == "hist":              # longest past episode; beyond experience or none -> continues
            if not done:
                return INF
            m = max(done)
            return m - tau if tau <= m else INF
        if prior == "hist-lindy":        # longest past episode; beyond experience or none -> lindy
            if not done:
                return tau
            m = max(done)
            return m - tau if tau <= m else tau
        if prior == "histmax-lindy":     # max(longest past - tau, tau)
            m = max(done) if done else 0.0
            return max(m - tau, tau)
        raise ValueError(prior)


# ------------------------------------------------------------------ policies
class Policy:
    """Tier before each batch. Subclasses override tier()."""
    name = "base"
    static = None          # "low"/"med"/"high" for pinned policies
    gmed, glow = 5.0, 3.0  # per-module budget gates of the best tier (inherited)
    gran = "stage"
    accept = "split"       # "split": per-module budget = slack / modules left (+ gates);
                           # "feas": upgrade only if every known frame stays feasible at lowest effort
    block_reserve = False  # feas: a module may block at most (tightest SLO seen - lowest-effort frame)
    path_k = None          # feas: a frame's planned completion within SLO / path_k of its arrival

    def setup(self, sc):
        self.sc = sc

    def tier(self, t, d, obs):
        return "best"


class Static(Policy):
    gran = "batch"

    def __init__(self, e):
        self.static, self.name = e, f"static-{e}"


class Ladder(Policy):
    """Depth thresholds Q_e = floor(SLO_r / (k s_e_max)). guard: None | horizon prior."""

    def __init__(self, k=2.0, guard=None, smax_over=0.3, gates=True, name=None, window_s=None,
                 accept="split", block_reserve=False, path_k=None, boundary="slow", escape=False,
                 cad_src="drain", qmode="worst", budget="slo", inep="slow", energy_feas=False,
                 energy_guard=False, cap_budget=False, nohist="inf", energy_time=False,
                 energy_service=False, energy_future=False):
        self.cad_src, self.qmode, self.budget, self.inep = cad_src, qmode, budget, inep
        # cap_budget: B = min(B, MAX_INGEST * S_low) -- a full queue is a deadline too
        #   (frames are dropped), drained at lowest effort in MAX_INGEST * S_low.
        # nohist: horizon of an overload episode with no (or exceeded) history:
        #   "inf" (continues) or "fill" (until the ingestion queue is full).
        self.cap_budget, self.nohist = cap_budget, nohist
        # energy_time: the feasibility walk also integrates the battery and inserts
        #   the recharge time (deficit / learned available power) wherever the queued
        #   work would take it below the floor; deadlines are checked with those waits.
        self.energy_time = energy_time
        # energy_service: every service time the policy uses (depth thresholds, guard,
        #   capacity budget) becomes energy-limited: s' = s + recharge time, with the
        #   power available P_avail = P_s (learned) + (W - floor) / B (stored energy
        #   spread over the policy's own budget); with a full battery s' = s.
        self.energy_service = energy_service
        # energy_future: the energy-timed walk continues past the queue with the frames
        #   the policy expects -- one per learned cadence until the current overload
        #   episode is expected to end (the guard's horizon), each at lowest effort with
        #   the largest recent deadline window -- so an upgrade that would leave them
        #   without energy is refused.
        self.energy_future = energy_future
        self.energy_feas, self.energy_guard = energy_feas, energy_guard
        self.k, self.guard, self.smax_over, self.window_s = k, guard, smax_over, window_s
        self.accept, self.block_reserve, self.path_k = accept, block_reserve, path_k
        self.boundary, self.escape = boundary, escape
        if not gates:
            self.gmed = self.glow = INF
        self.name = name or (f"ladder-k{k}" + (f"-{guard}" if guard else "") + ("" if gates else "-nogate") +
                             ("-feas" if accept == "feas" else "") + ("-blk" if block_reserve else "") +
                             (f"-path{path_k}" if path_k else "") +
                             ("-bklog" if boundary == "backlog" else "") + ("-esc" if escape else "") +
                             ("-arr" if cad_src == "arrival" else "") + ("" if qmode == "worst" else f"-q{qmode}"))

    def setup(self, sc):
        self.sc = sc
        c = sc["cost"]
        sh = c.frame("high", 99, 99, self.smax_over)
        sm = c.frame("med", 99, 99, self.smax_over)
        B = sc["slo_r"] / self.k
        self.q1, self.q2, self.B = math.floor(B / sh), math.floor(B / sm), B
        self.sh, self.sm = sh, sm
        self.eh, self.em = c.frame_en("high", 99, 99), c.frame_en("med", 99, 99)

    def s_energy(self, s, e_uwh, obs, B=None):
        """Energy-limited service time of a frame taking s seconds and e_uwh of module
        energy: s plus the time to recharge what it draws beyond the available power."""
        bat = obs.bat
        if not self.energy_service or bat is None or s is None or e_uwh is None:
            return s
        p_s = bat.avail_power_sofar(obs.now)
        if p_s is None:
            return s
        B = B if B is not None else self.B
        p_av = p_s + max(bat.W - bat.floor, 0.0) * 3600.0 / max(B, 1e-9)
        if p_av <= 0:
            return INF
        deficit = (e_uwh / 1e6 * 3600.0 + (bat.p_idle - p_av) * s) / 3600.0   # Wh
        return s + bat.recharge_s(deficit, p_av)

    def s_low(self, obs, B=None):
        return self.s_energy(obs.s("low"), obs.e_frame("low"), obs, B)

    def refresh(self, obs):
        """budget='frames': B = (largest deadline window among the last W frames) / k; no classes."""
        if self.budget == "frames" and obs.win:
            self.B = max(obs.win) / self.k
            if self.cap_budget and obs.s("low"):
                self.B = min(self.B, MAX_INGEST * self.s_low(obs, self.B))
            sh = self.s_energy(self.sh, self.eh, obs)
            sm = self.s_energy(self.sm, self.em, obs)
            self.q1, self.q2 = math.floor(self.B / sh), math.floor(self.B / sm)

    def guard_fires(self, t, d, obs):
        self.refresh(obs)
        if not self.guard:
            return False
        r = obs.rho("low")
        sl = obs.s("low")
        if self.energy_service and r is not None:
            sl = self.s_low(obs)
            r = sl / obs.c() if obs.c() else r
        if r is None or r < 1.0:
            return False
        if self.inep == "backlog":
            # overload continues while the gap since the last capture is shorter
            # than the time the lowest effort needs to drain what is queued
            if obs.last_arr is None or t - obs.last_arr > (d + 1) * sl:
                return False
        elif not obs.in_episode(t):
            return False
        w = d * sl
        if r == 1.0:
            return w >= self.B
        t_v = (self.B - w) / (r - 1.0)
        t_rem = obs.t_rem(t, "low", self.guard, self.window_s)
        if t_rem == INF and self.nohist == "fill":
            t_rem = max(MAX_INGEST - d, 0) * sl / (r - 1.0)
        return t_v < t_rem

    def tier(self, t, d, obs):
        self.refresh(obs)
        if self.guard_fires(t, d, obs):
            if self.escape and d < self.q2 and self.medium_fits(t, d, obs):
                return "capmed"
            return "lowest"
        q1, q2 = self.q1, self.q2
        cap = self.energy_cap(t, obs) if self.energy_guard else "best"
        if cap == "lowest":
            return "lowest"
        if self.qmode != "worst" and obs.svc["high"]:
            agg = statistics.median if self.qmode == "est" else max
            q1 = math.floor(self.B / agg(obs.svc["high"]))
            q2 = math.floor(self.B / agg(obs.svc["med"]))
        tier = "best" if d < q1 else ("capmed" if d < q2 else "lowest")
        rank = {"best": 0, "capmed": 1, "lowest": 2}
        return tier if rank[tier] >= rank[cap] else cap

    def energy_cap(self, t, obs):
        """E3: highest tier whose energy draw would not take the battery to the floor
        before the current overload at that effort is expected to end. Available
        power is learned from the battery trend over the last orbit (no profile)."""
        bat = obs.bat
        if bat is None:
            return "best"
        if bat.full_recently(t):
            return "best"                  # energy is not what limits: the battery filled up this orbit
        p_av = bat.avail_power(t)
        if p_av is None:
            return "best"
        c = obs.c()
        for e, tier in (("high", "best"), ("med", "capmed")):
            s_e, en_e = obs.s(e), obs.e_frame(e)
            if s_e is None or en_e is None or c is None:
                return tier
            p_dem = (en_e / 1e6 * 3600.0 + bat.p_idle * s_e) / max(c, s_e)    # W while frames keep coming
            drain = p_dem - p_av
            if drain <= 0 or not obs.in_episode(t, e):
                return tier
            t_floor = max(bat.W - bat.floor, 0.0) * 3600.0 / drain
            if t_floor >= obs.t_rem(t, e, "hist"):
                return tier
        return "lowest"

    def medium_fits(self, t, d, obs):
        """Medium's own projected backlog over the expected remaining episode stays within B."""
        r, sm = obs.rho("med"), obs.s("med")
        if r is None or sm is None:
            return False
        grow = 0.0
        if obs.in_episode(t, "med") and r > 1.0:
            tr = obs.t_rem(t, "med", self.guard, self.window_s)
            if tr == INF:
                return False
            grow = (r - 1.0) * tr
        return d * sm + grow <= self.B


class Greedy(Policy):
    name = "greedy"

    def tier(self, t, d, obs):
        return "best"


class Fluid(Policy):
    """Highest effort e whose projected backlog stays within B = SLO_r / k:
        D * S_e + [in overload episode at e] * max(rho_e - 1, 0) * T_rem_e  <=  B
    S_e, rho_e from recent frames (content-aware); episodes and T_rem per
    effort (prior). qcap: also respect the worst-case depth thresholds.
    Before any service estimate exists, falls back to the Q rule."""

    def __init__(self, k=2.0, prior="hist", gates=True, name=None, qcap=False, guard_low=None,
                 accept="split", block_reserve=False, path_k=None):
        self.k, self.prior, self.qcap, self.guard_low = k, prior, qcap, guard_low
        self.accept, self.block_reserve, self.path_k = accept, block_reserve, path_k
        self.lad = Ladder(k=k, guard=guard_low)
        if not gates:
            self.gmed = self.glow = INF
        self.name = name or (f"fluid-k{k}-{prior}" + ("-qcap" if qcap else "") +
                             (f"-gl{guard_low}" if guard_low else "") + ("" if gates else "-nogate") +
                             ("-feas" if accept == "feas" else "") + ("-blk" if block_reserve else "") +
                             (f"-path{path_k}" if path_k else ""))

    def setup(self, sc):
        self.sc = sc
        self.lad.setup(sc)
        self.B = sc["slo_r"] / self.k

    def tier(self, t, d, obs):
        if obs.s("low") is None or obs.c() is None:
            return self.lad.tier(t, d, obs)
        if self.guard_low and self.lad.guard_fires(t, d, obs):
            return "lowest"
        cap = self.lad.tier(t, d, obs) if self.qcap else "best"
        rank = {"best": 0, "capmed": 1, "lowest": 2}
        for e, tier in (("high", "best"), ("med", "capmed")):
            if rank[tier] < rank[cap]:
                continue
            grow = 0.0
            if obs.in_episode(t, e):
                r = obs.rho(e)
                if r > 1.0:
                    tr = obs.t_rem(t, e, self.prior)
                    if tr == INF:
                        continue
                    grow = (r - 1.0) * tr
            if d * obs.s(e) + grow <= self.B:
                return tier
        return "lowest"


# ------------------------------------------------------------------ simulation
def simulate(sc, pol):
    """sc: dict(arrivals=[(arr, slo, cls, nseg, nenc)], slo_r, cost, over, ingest_s, ckpt, window)."""
    cost, over, ingest_s, ckpt = sc["cost"], sc["over"], sc["ingest_s"], sc["ckpt"]
    model = sc.get("model") or cost
    jit = sc.get("jitter", 0.0)
    jrng = random.Random(sc["p"].get("seed", 0) * 7919 + 13) if jit else None
    pol.setup({**sc, "cost": model})
    oerr = sc.get("over_err", 1.0)               # policy's belief about per-frame overhead
    over_m, ingest_m = over * oerr, ingest_s * oerr
    obs = Obs(model, over_m, ingest_m, sc["window"], getattr(pol, "boundary", "slow"))
    obs.cad_src = getattr(pol, "cad_src", "drain")
    fr = [Frame(i, a, s, c, ns, ne) for i, (a, s, c, ns, ne) in enumerate(sc["arrivals"])]
    ingest, partial, dropped = [], [], []
    t, nxt = 0.0, 0
    one_mod_gran = pol.gran == "stage"
    E = sc.get("energy")
    bat = Battery(**E) if E else None
    if bat is not None:
        bat.p_budget = sc.get("p_budget")          # stated orbit-average budget (W) or None
    obs.bat = bat
    halt = {"energy": False}

    rec = sc.get("record")                       # optional list: (t, W above floor, depth, busy)

    def tick(t_new, busy):
        """Advance simulated time, integrating the battery."""
        nonlocal t
        if bat:
            bat.advance(t, t_new, busy)
        t = t_new
        if rec is not None:
            rec.append((t, (bat.W - bat.floor) if bat else None, len(ingest) + len(partial), busy))

    def en_est(g, m, e):
        if m == "demosaic":
            return model.en(1, 1)
        if m == "tile":
            return model.en(2, 1)
        return model.en(SLOT[m][e], est_n(g, m))

    def en_true(f, m, e):
        if m == "demosaic":
            return cost.en(1, 1)
        if m == "tile":
            return cost.en(2, 1)
        return cost.en(SLOT[m][e], n_of(f, m))

    def energy_ok(f, m, e):
        if bat is None:
            return True
        ok = bat.W - en_est(f, m, e) / 1e6 >= bat.floor
        if not ok:
            halt["energy"] = True
        return ok

    def admit():
        nonlocal nxt, t
        k0 = nxt
        while nxt < len(fr) and fr[nxt].arr <= t + 1e-9:
            f = fr[nxt]; nxt += 1
            obs.note_arrival(f.arr, len(ingest) + len(partial))
            obs.min_slo = min(obs.min_slo, f.dl - f.arr)
            obs.win.append(f.dl - f.arr)
            if len(ingest) >= MAX_INGEST:
                dropped.append(f)
            else:
                ingest.append(f)
                tick(t + ingest_s, True)
        if nxt > k0:
            obs.note_drain(t, nxt - k0)

    def n_of(f, mod):
        if mod in ("demosaic", "tile"):
            return 1
        if mod == "cls":
            return 99
        if mod == "seg":
            return f.nseg
        return f.nenc

    def lat(f, mod, e):
        """True execution time (with per-execution jitter)."""
        if mod == "demosaic":
            v = cost.lat(1, 1)
        elif mod == "tile":
            v = cost.lat(2, 1)
        else:
            v = cost.lat(SLOT[mod][e], n_of(f, mod))
        return v * jrng.lognormvariate(-jit * jit / 2, jit) if jit else v

    def est_n(g, m):
        """Input size of module m for frame g: known once the earlier module ran, else recent median."""
        if m in ("demosaic", "tile"):
            return 1
        if m == "cls":
            return 99
        if m == "seg":
            return g.nseg if g.prog >= MODULES.index("cls") else (int(statistics.median(obs.nseg)) if obs.nseg else 99)
        return g.nenc if g.prog >= MODULES.index("seg") else (int(statistics.median(obs.nenc)) if obs.nenc else 99)

    def lat_est(g, m, e):
        if m == "demosaic":
            return model.lat(1, 1)
        if m == "tile":
            return model.lat(2, 1)
        return model.lat(SLOT[m][e], est_n(g, m))

    def rest_low(g, from_idx):
        """Lowest-effort time for g's modules from index from_idx on (+ batch overhead if not started)."""
        x = sum(lat_est(g, MODULES[j], "low") for j in range(from_idx, len(MODULES)))
        return x + (over_m if g.prog == -1 and from_idx == 0 else 0.0)

    def e_rest_low(g, from_idx):
        return sum(en_est(g, MODULES[j], "low") for j in range(from_idx, len(MODULES)))

    def feasible_energy_timed(f, mod, e, now, mine, others):
        """EDF walk over the queued frames (f at effort e for this module, everything
        else at lowest) with the battery integrated: work earns the learned available
        power P_s and spends module energy plus the board's idle power; when the floor
        would be crossed, the walk waits (deficit / P_s) -- the payload is shed at the
        floor, so it recharges at P_s. Before P_s is known (one orbit) no recharge is
        assumed: an upgrade that needs one is refused."""
        p_s = bat.avail_power_sofar(now)
        i = MODULES.index(mod)
        W = bat.W
        tc = now

        def step(dur, e_uwh):
            nonlocal W, tc
            need = e_uwh / 1e6 + (bat.p_idle - (p_s or 0.0)) * dur / 3600.0
            if W - need < bat.floor:
                if not p_s or p_s <= 0:
                    return False
                w = bat.recharge_s(bat.floor + need - W, p_s)
                if w == INF:
                    return False
                tc += w
                W = bat.floor + need
            W -= need
            tc += dur
            return True

        e_mine = en_est(f, mod, e) + e_rest_low(f, i + 1)
        placed = False
        for g in others:
            if not placed and f.dl <= g.dl:
                if not step(mine, e_mine) or tc > f.dl + 1e-9:
                    return False
                placed = True
            before = tc
            if not step(rest_low(g, g.prog + 1), e_rest_low(g, g.prog + 1)):
                return False
            if tc > g.dl + 1e-9:
                if placed and before - mine > g.dl + 1e-9 - rest_low(g, g.prog + 1):
                    continue          # g was late at lowest even without this upgrade
                return False
        if not placed:
            if not step(mine, e_mine) or tc > f.dl + 1e-9:
                return False
        if getattr(pol, "energy_future", False):
            c, sl, el = obs.c(), obs.s("low"), obs.e_frame("low")
            if c and sl and el and obs.last_arr is not None and obs.win:
                win = max(obs.win)
                # overload judged on the energy-limited lowest-effort service time: a
                # frame stream can be sustainable in time and not in energy
                se = sl
                p_s = bat.avail_power_sofar(now)
                if p_s is not None:
                    p_av = p_s + max(bat.W - bat.floor, 0.0) * 3600.0 / max(pol.B, 1e-9)
                    room = p_av - bat.p_idle
                    se = max(sl, el / 1e6 * 3600.0 / room) if room > 0 else INF
                r = se / c
                t_rem = obs.t_rem(now, "low", "hist")
                if t_rem == INF:          # no history: until the queue would be full
                    depth = len(ingest) + len(partial)
                    t_rem = (max(MAX_INGEST - depth, 0) * se / (r - 1.0) if r < INF else win) if r > 1.0 else win
                # look ahead as far as the policy plans (its budget B), and stop once
                # the queue has drained and the battery is back where it started:
                # from there on the upgrade has been paid for
                horizon = t_rem if getattr(pol, "future_full", False) else min(t_rem, pol.B)
                w_start = bat.W
                a = obs.last_arr + c
                while a <= now + horizon:
                    if tc < a:            # idle until it arrives: the board's idle draw continues
                        if not step(a - tc, 0.0):
                            return False
                        if W >= w_start:
                            break
                    if not step(sl, el) or tc > a + win + 1e-9:
                        return False
                    a += c
        return True

    def feasible(f, mod, e, now):
        i = MODULES.index(mod)
        mine = lat_est(f, mod, e) + rest_low(f, i + 1) + (over_m if f.prog == -1 else 0.0)
        if pol.block_reserve and obs.min_slo < INF:
            # Room for one more tightest-deadline frame arriving now: it preempts f at the next
            # module boundary only if f's deadline is later than its own; otherwise it waits
            # for f's whole remaining path. Either way it then runs at lowest effort.
            new_dl = now + obs.min_slo
            block = lat_est(f, mod, e) if f.dl > new_dl else mine
            if block + (obs.s("low") or 0.0) > obs.min_slo + 1e-9:
                return False
        if pol.path_k and (now + mine - f.arr) > (f.dl - f.arr) / pol.path_k + 1e-9:
            return False          # a frame's own planned path may use at most 1/k of its deadline
        others = sorted((g for g in ingest + partial if g is not f), key=lambda g: g.dl)
        if bat is not None and getattr(pol, "energy_time", False):
            if not feasible_energy_timed(f, mod, e, now, mine, others):
                return False
            if not getattr(pol, "energy_feas", False):
                return True
        tc = now
        placed = False
        for g in others:
            if not placed and f.dl <= g.dl:
                tc += mine
                if tc > f.dl + 1e-9:
                    return False
                placed = True
            tc += rest_low(g, g.prog + 1)
            if tc > g.dl + 1e-9:
                # g is already infeasible at lowest even without this upgrade? then do not blame it
                if tc - mine > g.dl + 1e-9 and placed:
                    continue
                return False
        if not placed:
            tc += mine
            if tc > f.dl + 1e-9:
                return False
        if bat is not None and getattr(pol, "energy_feas", False):
            # E1: after this upgrade, the energy above the floor must still cover every known
            # frame at lowest effort, including the board's idle power while it works through them
            e_mods = en_est(f, mod, e) + sum(en_est(f, MODULES[j], "low") for j in range(i + 1, len(MODULES)))
            e_mods += sum(en_est(g, MODULES[j], "low") for g in others for j in range(g.prog + 1, len(MODULES)))
            t_work = tc - now
            need = e_mods / 1e6 + bat.p_idle * t_work / 3600.0
            if bat.W - need < bat.floor:
                return False
        return True

    def decide(f, mod, mode, now):
        if mod in ("demosaic", "tile"):
            return "default" if energy_ok(f, mod, "default") else None
        if pol.static:
            return pol.static if energy_ok(f, mod, pol.static) else None
        if pol.accept == "feas":
            if mode == "lowest":
                return "low" if energy_ok(f, mod, "low") else None
            order = ("med", "low") if mode == "capmed" else ("high", "med", "low")
            for e in order:
                if e == "low":
                    return "low" if energy_ok(f, mod, "low") else None
                if feasible(f, mod, e, now):
                    if energy_ok(f, mod, e):
                        return e
                    if getattr(pol, "energy_time", False) and bat is not None:
                        # feasible including the recharge wait: wait for it rather than
                        # spending the deadline slack on a lower effort now (the halt
                        # then lasts until this module's energy is there)
                        halt["need"] = max(halt.get("need", 0.0), en_est(f, mod, e) / 1e6)
                        return None
            return None
        left = len(MODULES) - MODULES.index(mod)
        budget = (f.dl - now) / left
        if mode == "lowest":
            return "low" if energy_ok(f, mod, "low") else None
        if mode == "capmed":
            order = [("med", None), ("low", None)]
        else:
            order = [("high", None), ("med", pol.gmed), ("low", pol.glow)]
        for e, gate in order:
            if gate is not None and not budget < gate:
                continue
            if e == "low":
                return "low" if energy_ok(f, mod, "low") else None
            if lat_est(f, mod, e) <= budget and energy_ok(f, mod, e):
                return e
        return None

    def run_one(src, one_module):
        nonlocal t
        if not src:
            return 0
        src.sort(key=lambda f: f.dl)
        f = src[0]
        obs.now = t
        mode = "best" if pol.static else pol.tier(t, len(ingest) + len(partial), obs)
        prog0 = f.prog
        while f.prog + 1 < len(MODULES):
            mod = MODULES[f.prog + 1]
            e = decide(f, mod, mode, t)
            if e is None:
                break
            if e != "default":
                f.eff[mod] = e
            tick(t + lat(f, mod, e) + (over if f.prog == -1 else 0.0), True)
            if bat:
                bat.debit(en_true(f, mod, e))
            f.prog += 1
            if mod == "seg":
                obs.note_frame(f)
            if one_module and f.prog + 1 < len(MODULES):
                admit()
                if any(o.dl < f.dl for o in ingest + partial if o is not f):
                    break
        if f.prog + 1 >= len(MODULES):
            f.done = t
            src.remove(f)
            return 1
        made = f.prog != prog0
        if src is partial:
            if one_module and made:
                tick(t + ckpt, True); f.parked += 1
            return 1 if made else 2
        # both queues share the daemon's MAX_QUEUE_SIZE (= MAX_INGEST); MAX_PARTIAL
        # only limits parking by yield (one_module) and the batch-mode drain
        if len(partial) < MAX_INGEST:
            ingest.remove(f); partial.append(f)
            if one_module and made:
                tick(t + ckpt, True); f.parked += 1
        return 1 if made else 2

    def wait_until():
        """Nothing could run. Without an energy halt: poll (as before). With one: jump to the
        next arrival, or until the battery has charged enough for the cheapest next module,
        or to the next sunlit/eclipse boundary -- whichever is first."""
        nxt_arr = fr[nxt].arr if nxt < len(fr) else INF
        if not (bat and halt["energy"]):
            return min(t + HALT_POLL, nxt_arr) if nxt_arr > t else t + HALT_POLL
        halt["energy"] = False
        pool = ingest + partial
        if not pool:
            return min(nxt_arr, t + HALT_POLL)
        g = min(pool, key=lambda x: x.dl)
        m = MODULES[g.prog + 1]
        need = en_est(g, m, "low" if m not in ("demosaic", "tile") else "default") / 1e6 + 1e-6
        # a policy that waits for a higher effort (energy_time) waits for that module's energy
        need = max(need, halt.pop("need", 0.0) + 1e-6)
        deficit = bat.floor + need - bat.W
        board = bat.p_idle if bat.board_on_seconds(t, t + 1, False) > 0 else 0.0
        rate = (bat.p_sun if bat.sunlit_now(t) else 0.0) - bat.bus - board      # W
        t_b = bat.next_boundary(t)
        cand = [nxt_arr, t_b]
        if rate > 0:
            cand.append(t + max(deficit, 0.0) * 3600.0 / rate + 0.01)
        if board > 0 and bat.mode != "always":
            cand.append(bat.last_active + bat.hold + 1e-3)
        tn = min(c for c in cand if c > t + 1e-9) if any(c > t + 1e-9 for c in cand) else t + HALT_POLL
        return tn

    last_dl = max(f.dl for f in fr) if fr else 0.0
    while nxt < len(fr) or ingest or partial:
        if nxt >= len(fr) and t > last_dl:
            break                              # every remaining frame is already late: they count as missed
        admit()
        if not ingest and not partial:
            tick(max(t, fr[nxt].arr), False)
            continue
        if one_mod_gran:
            pool = ingest + partial
            head = min(pool, key=lambda f: f.dl)
            r = run_one(ingest if head in ingest else partial, len(partial) < MAX_PARTIAL or head in partial)
            if r != 1:
                other = partial if head in ingest else ingest
                r2 = run_one(other, len(partial) < MAX_PARTIAL) if other else 0
                if r2 != 1:
                    tick(wait_until(), False)
            continue
        rp = run_one(partial, False)
        ri = 0 if rp == 1 else run_one(ingest, False)
        if rp != 1 and ri != 1:
            tick(wait_until(), False)
            continue
        if rp == 1 and len(partial) < MAX_PARTIAL:
            admit()
            run_one(ingest, False)
    out = summarise(fr)
    out["cap_dropped"] = len(dropped)            # refused at the ingest cap (INGEST_DROP)
    if rec is not None:
        out["frames"] = [(f.arr, f.dl, f.done, f.cls, dict(f.eff)) for f in fr]
    if bat:
        out["minW"] = bat.min_W - bat.floor          # lowest margin above the floor reached (Wh)
    return out


def summarise(fr):
    n = len(fr)
    on = [f for f in fr if f.done is not None and f.done <= f.dl + 1e-9]
    U = sum(quality(f) for f in on) / n
    urg = [f for f in fr if f.cls == "urgent"]
    uh = sum(1 for f in urg if f.done is not None and f.done <= f.dl + 1e-9) / len(urg) if urg else 1.0
    rou = [f for f in fr if f.cls != "urgent"]
    rh = sum(1 for f in rou if f.done is not None and f.done <= f.dl + 1e-9) / len(rou) if rou else 1.0
    mix = collections.Counter(f.eff.get("cls") for f in fr if f.done is not None)
    # dropped = every frame that never completed (cap drops and frames left at the end)
    return {"U": U, "urgent_hit": uh, "routine_hit": rh, "dropped": sum(1 for f in fr if f.done is None),
            "f_high": mix.get("high", 0) / n, "f_med": mix.get("med", 0) / n}


# ------------------------------------------------------------------ scenarios
_BASE = None


def base_cost():
    global _BASE
    if _BASE is None:
        _BASE = S.Cost(os.path.join(ROOT, "analysis/cost_model.json"))
    return _BASE


def content_draw(rng, w, c):
    nw = sum(1 for _ in range(99) if rng.random() < w)
    nc = sum(1 for _ in range(nw) if rng.random() < c)
    return nw, nc


def make_scenario(seed, **fixed):
    """Random scenario; any parameter can be fixed by keyword."""
    rng = random.Random(seed)
    p = {}
    p["period"] = fixed.get("period", math.exp(rng.uniform(math.log(1.8), math.log(12.0))))
    p["burst_mean"] = fixed.get("burst_mean", math.exp(rng.uniform(math.log(3), math.log(600))))
    p["burst_cv"] = fixed.get("burst_cv", rng.choice([0.0, 0.5, 1.0, 1.5]))
    p["gap"] = fixed.get("gap", math.exp(rng.uniform(math.log(15), math.log(6000))))
    p["slo_r"] = fixed.get("slo_r", math.exp(rng.uniform(math.log(60), math.log(2400))))
    p["slo_u"] = fixed.get("slo_u", 20.0)
    p["urgent"] = fixed.get("urgent", rng.choice([0.0, 0.05, 0.1, 0.2, 0.4]))
    p["water"] = fixed.get("water", rng.uniform(0.05, 1.0))
    p["clear"] = fixed.get("clear", rng.uniform(0.0, 1.0))
    p["content_var"] = fixed.get("content_var", rng.choice(["const", "per_burst", "shift"]))
    p["hi_scale"] = fixed.get("hi_scale", rng.choice([1.0, 1.0, 1.0, 1.5, 2.0]))
    p["frames_target"] = fixed.get("frames_target", 900)
    p["model_err"] = fixed.get("model_err", 0.0)     # sigma of log per-configuration model error
    p["jitter"] = fixed.get("jitter", 0.0)           # sigma of log per-execution latency noise
    p["window"] = fixed.get("window", 8)             # estimator window (frames)
    p["arrival_cv"] = fixed.get("arrival_cv", 0.03)  # in-burst spacing variability (0.03 ~ the old +-5 %)
    # energy (off unless energy=1): generic square-wave generation, no mission profile
    p["energy"] = fixed.get("energy", 0)
    if p["energy"]:
        # own random stream: the time scenario (arrivals, content) is identical with and without energy
        er = random.Random(seed * 1000003 + 17)
        # orbit-average generation minus platform bus load, before the payload; > 0 (a platform that
        # cannot carry its own bus is not a regime); DISCO-2: +3.4 W BOL, +2.2 W EOL
        p["e_surplus"] = fixed.get("e_surplus", er.uniform(0.2, 5.0))
        p["e_sunfrac"] = fixed.get("e_sunfrac", er.uniform(0.55, 0.75))
        p["e_board"] = fixed.get("e_board", er.choice(["burst", "always"]))
        p["e_floor_orbits"] = fixed.get("e_floor_orbits", er.choice([1, 2, 2, 3]))
        p["e_soc0"] = fixed.get("e_soc0", er.uniform(0.0, 1.0))          # fraction of (cap - floor) above floor
        p["e_phase0"] = fixed.get("e_phase0", er.uniform(0, 1))
    p["over_err"] = fixed.get("over_err", 1.0)       # policy-side overhead / true overhead
    p["seed"] = seed
    return build(p, rng)


def build(p, rng=None):
    rng = rng or random.Random(p["seed"])
    arrivals, t, i = [], 0.0, 0
    nb = max(2, min(60, round(p["frames_target"] / p["burst_mean"])))
    in_urgent = 0
    for b in range(nb):
        if p["burst_cv"] > 0:
            s2 = math.log(1 + p["burst_cv"] ** 2)
            L = max(1, round(rng.lognormvariate(math.log(p["burst_mean"]) - s2 / 2, math.sqrt(s2))))
        else:
            L = max(1, round(p["burst_mean"]))
        if p["content_var"] == "per_burst":
            w = min(1.0, max(0.02, rng.gauss(p["water"], 0.25)))
            c = min(1.0, max(0.0, rng.gauss(p["clear"], 0.3)))
        else:
            w, c = p["water"], p["clear"]
        for j in range(L):
            if p["content_var"] == "shift":
                # first half as drawn, second half clear water (the overload the plan cannot know)
                ww, cc = (w, c) if j < L / 2 else (max(w, 0.9), max(c, 0.9))
            else:
                ww, cc = w, c
            nw, nc = content_draw(rng, ww, cc)
            if in_urgent > 0:
                cls = "urgent"; in_urgent -= 1
            elif p["urgent"] > 0 and rng.random() < p["urgent"] / 3.0:
                cls = "urgent"; in_urgent = int(rng.expovariate(1 / 2.0))
            else:
                cls = "routine"
            arrivals.append((t, p["slo_u"] if cls == "urgent" else p["slo_r"], cls, nw, nc))
            cv = p.get("arrival_cv", 0.03)
            if cv <= 0.05:
                t += p["period"] * (1.0 + 0.05 * rng.uniform(-1, 1))
            else:   # gamma-distributed spacing with mean = period, coefficient of variation cv (1 = Poisson)
                sh = 1.0 / (cv * cv)
                t += rng.gammavariate(sh, p["period"] / sh)
            i += 1
        t += p["gap"]
    true = Cost(base_cost(), p["hi_scale"])
    energy = None
    if p.get("energy"):
        cap, bus, T = 92.0, 13.07, 5725.8
        floor = p["e_floor_orbits"] * bus * T / 3600.0
        energy = dict(cap=cap, floor=floor, w0=floor + 0.5 + p["e_soc0"] * (cap - floor - 0.5), bus=bus,
                      p_sun=(bus + p["e_surplus"]) / p["e_sunfrac"], sunfrac=p["e_sunfrac"], t_orb=T,
                      phase0=p["e_phase0"], p_idle=3.30, hold=60.0, mode=p["e_board"])
    sc = {"p": p, "arrivals": arrivals, "slo_r": p["slo_r"], "cost": true, "energy": energy,
          "p_budget": p.get("e_surplus") if energy else None,
          "over": 0.30, "ingest_s": 0.41, "ckpt": 0.5, "window": int(p.get("window", 8)),
          "jitter": p.get("jitter", 0.0), "over_err": p.get("over_err", 1.0)}
    if p.get("model_err", 0.0) > 0:
        erng = random.Random(p["seed"] * 31 + 7)
        sig = p["model_err"]
        sc["model"] = ModelCost(true, {sl: math.exp(erng.gauss(0, sig)) for sl in range(1, 21)})
    return sc


def scenario_features(sc):
    c = sc["cost"]; p = sc["p"]
    ns = [a[3] for a in sc["arrivals"]]; ne = [a[4] for a in sc["arrivals"]]
    mns, mne = statistics.mean(ns), statistics.mean(ne)
    o = sc["over"] + sc["ingest_s"]
    feat = {f"s_{e}": c.frame(e, round(mns), round(mne), o) for e in EFF}
    for e in EFF:
        feat[f"rho_{e}"] = feat[f"s_{e}"] / p["period"]
    # backlog the longest burst would build at each effort, relative to the routine deadline
    L = max(sum(1 for _ in g) for _, g in _bursts(sc))
    for e in EFF:
        feat[f"pressure_{e}"] = max(feat[f"s_{e}"] - p["period"], 0.0) * L / p["slo_r"]
    feat["longest_burst"] = L
    feat["n_frames"] = len(sc["arrivals"])
    return feat


def _bursts(sc):
    import itertools
    gaps = [0] + [1 if sc["arrivals"][i][0] - sc["arrivals"][i - 1][0] > 3 * sc["p"]["period"] else 0
                  for i in range(1, len(sc["arrivals"]))]
    bid = list(itertools.accumulate(gaps))
    return itertools.groupby(range(len(bid)), key=lambda i: bid[i])


# ------------------------------------------------------------------ policy set
def policy_set(name):
    if name == "fam1":
        out = [Static("low"), Static("med"), Static("high"), Greedy(), Ladder(), Ladder(guard="hist")]
        for k in (1.0, 1.5, 2.0):
            for prior in ("zero", "lindy", "hist", "hist-lindy", "histmax-lindy"):
                out.append(Fluid(k=k, prior=prior))
            out.append(Fluid(k=k, prior="hist-lindy", qcap=True))
            out.append(Fluid(k=k, prior="hist-lindy", guard_low="hist"))
        return out
    if name == "fam2":
        out = [Static("low"), Static("med"), Static("high"), Greedy(), Ladder(), Ladder(guard="hist")]
        for acc, blk in (("feas", False), ("feas", True)):
            out += [Ladder(accept=acc, block_reserve=blk),
                    Ladder(guard="hist", accept=acc, block_reserve=blk),
                    Ladder(guard="hist-lindy", accept=acc, block_reserve=blk),
                    Ladder(k=1.5, guard="hist", accept=acc, block_reserve=blk),
                    Ladder(k=3.0, guard="hist", accept=acc, block_reserve=blk),
                    Fluid(prior="hist-lindy", qcap=True, accept=acc, block_reserve=blk),
                    Fluid(prior="hist", qcap=True, accept=acc, block_reserve=blk)]
        return out
    if name == "fam3":
        return [Static("low"), Static("med"), Static("high"), Greedy(), Ladder(), Ladder(guard="hist"),
                Ladder(guard="hist", accept="feas"), Ladder(guard="hist", accept="feas", block_reserve=True),
                Ladder(accept="feas", block_reserve=True),
                Ladder(k=3.0, guard="hist", accept="feas", block_reserve=True),
                Fluid(prior="hist", qcap=True, accept="feas", block_reserve=True)]
    if name == "fam4":
        return [Static("low"), Static("med"), Static("high"), Greedy(), Ladder(guard="hist"),
                Ladder(guard="hist", accept="feas"),
                Ladder(guard="hist", accept="feas", path_k=2.0),
                Ladder(guard="hist", accept="feas", path_k=2.0, block_reserve=True),
                Ladder(guard="hist", accept="feas", path_k=1.5),
                Ladder(guard="hist", accept="feas", path_k=3.0),
                Ladder(accept="feas", path_k=2.0)]
    if name == "fam5":
        base = dict(guard="hist", accept="feas", path_k=2.0)
        return [Static("low"), Static("med"), Static("high"), Greedy(),
                Ladder(**base), Ladder(**base, boundary="backlog"), Ladder(**base, escape=True),
                Ladder(**base, boundary="backlog", escape=True),
                Ladder(guard="hist-lindy", accept="feas", path_k=2.0, boundary="backlog"),
                Ladder(guard="hist-lindy", accept="feas", path_k=2.0, boundary="backlog", escape=True)]
    if name == "final":
        c = dict(guard="hist", accept="feas", path_k=2.0)
        return [Static("low"), Static("med"), Static("high"), Greedy(),
                Ladder(name="ladder-as-evaluated"),
                Ladder(guard="inf", name="ladder-rho-guard"),
                Ladder(guard="hist", name="ladder-hist"),
                Ladder(**c, name="C1"),
                Ladder(**c, boundary="backlog", name="C2"),
                Ladder(k=1.5, guard="hist", accept="feas", path_k=1.5, boundary="backlog", name="C2-k1.5"),
                Ladder(k=3.0, guard="hist", accept="feas", path_k=3.0, boundary="backlog", name="C2-k3")]
    if name == "fam6":
        c2 = dict(guard="hist", accept="feas", path_k=2.0, boundary="backlog")
        return [Static("low"), Static("med"), Static("high"), Ladder(name="ladder-as-evaluated"),
                Ladder(**c2, name="C2"), Ladder(**c2, cad_src="arrival", name="C3"),
                Ladder(guard="hist", accept="feas", path_k=2.0, boundary="slow", cad_src="arrival", name="C3-slow")]
    if name == "fam7":
        c3 = dict(guard="hist", accept="feas", path_k=2.0, boundary="backlog", cad_src="arrival")
        return [Static("low"), Static("med"), Static("high"), Ladder(name="ladder-as-evaluated"),
                Ladder(**c3, name="C3"), Ladder(**c3, qmode="est", name="C3-qest"),
                Ladder(**c3, qmode="max", name="C3-qmax"),
                Ladder(k=1.5, guard="hist", accept="feas", path_k=1.5, boundary="backlog", cad_src="arrival", name="C3-k1.5"),
                Ladder(k=1.5, guard="hist", accept="feas", path_k=2.0, boundary="backlog", cad_src="arrival", qmode="max", name="C3-k1.5-qmax")]
    if name == "final2":
        c2 = dict(guard="hist", accept="feas", path_k=2.0, boundary="backlog")
        return [Static("low"), Static("med"), Static("high"), Greedy(),
                Ladder(name="ladder-as-evaluated"), Ladder(guard="inf", name="ladder-rho-guard"),
                Ladder(**c2, name="C2"), Ladder(**c2, cad_src="arrival", name="C3")]
    if name == "c3":
        return [Static("low"), Static("med"), Static("high"), Ladder(name="ladder-as-evaluated"),
                Ladder(guard="hist", accept="feas", path_k=2.0, boundary="backlog", cad_src="arrival", name="C3")]
    if name == "c3c4":
        c3 = dict(guard="hist", accept="feas", path_k=2.0, boundary="backlog", cad_src="arrival")
        return [Static("low"), Static("med"), Static("high"), Ladder(name="ladder-as-evaluated"),
                Ladder(**c3, name="C3"), Ladder(**c3, budget="frames", name="C4")]
    if name == "c4c5":
        c4 = dict(guard="hist", accept="feas", path_k=2.0, boundary="backlog", cad_src="arrival", budget="frames")
        return [Static("low"), Static("med"), Static("high"), Ladder(name="ladder-as-evaluated"),
                Ladder(**c4, name="C4"), Ladder(**c4, inep="backlog", name="C5")]
    if name == "energy":
        c4 = dict(guard="hist", accept="feas", path_k=2.0, boundary="backlog", cad_src="arrival", budget="frames")
        return [Static("low"), Static("med"), Static("high"), Greedy(), Ladder(name="ladder-as-evaluated"),
                Ladder(**c4, name="v2"), Ladder(**c4, energy_feas=True, name="v2+E1"),
                Ladder(**c4, energy_guard=True, name="v2+E3"),
                Ladder(**c4, energy_feas=True, energy_guard=True, name="v2+E1+E3")]
    if name in ("final3", "final3-sens", "final3-energy"):
        # FROZEN 2026-10-14 (paper-rev/policy_final.md): "sipp" = the board-evaluated
        # v2 + capacity-aware budget + queue-fill horizon + energy-timed feasibility
        # with the wait rule. "v2-board" = the policy evaluated on the board.
        vb = dict(guard="hist", accept="feas", boundary="backlog", cad_src="arrival", budget="frames", path_k=2.0)
        fin = dict(vb, cap_budget=True, nohist="fill", energy_time=True)
        out = [Static("low"), Static("med"), Static("high"), Greedy(), Ladder(name="ladder-as-evaluated"),
               Ladder(**vb, name="v2-board"), Ladder(**fin, name="sipp")]
        if name == "final3-sens":
            out += [Ladder(**dict(fin, k=1.5, path_k=1.5), name="sipp-k1.5"),
                    Ladder(**dict(fin, k=3.0, path_k=3.0), name="sipp-k3")]
        if name == "final3-energy":
            out += [Ladder(**dict(vb, cap_budget=True, nohist="fill"), name="sipp-noET"),
                    Ladder(**dict(fin, energy_feas=True), name="sipp+E1")]
        return out
    if name == "capfix":
        v2 = dict(guard="hist", accept="feas", boundary="backlog", cad_src="arrival", budget="frames", path_k=2.0)
        return [Static("low"), Static("med"), Static("high"), Greedy(), Ladder(name="ladder-as-evaluated"),
                Ladder(**v2, name="v2"), Ladder(**v2, cap_budget=True, name="v2-cap"),
                Ladder(**v2, nohist="fill", name="v2-fill"),
                Ladder(**v2, cap_budget=True, nohist="fill", name="v2-cap-fill")]
    if name == "sens2":
        v2 = dict(guard="hist", accept="feas", boundary="backlog", cad_src="arrival", budget="frames")
        return [Static("low"), Static("med"), Static("high"), Ladder(name="ladder-as-evaluated"),
                Ladder(**v2, path_k=2.0, name="v2"),
                Ladder(**v2, k=1.5, path_k=1.5, name="v2-k1.5"),
                Ladder(**v2, k=3.0, path_k=3.0, name="v2-k3")]
    if name == "sens":
        return [Static("low"), Static("med"), Static("high"), Ladder(name="ladder-as-evaluated"),
                Ladder(guard="hist", accept="feas", path_k=2.0, boundary="backlog", name="C2")]
    if name == "core":
        return [Static("low"), Static("med"), Static("high"), Greedy(),
                Ladder(), Ladder(guard="inf"), Ladder(guard="hist"), Ladder(guard="lindy"),
                Ladder(guard="hist-lindy"),
                Fluid(prior="inf"), Fluid(prior="hist"), Fluid(prior="lindy"), Fluid(prior="hist-lindy")]
    raise ValueError(name)


def run_job(args):
    seed, fixed, pset = args
    sc = make_scenario(seed, **fixed)
    out = {"seed": seed, **{k: v for k, v in sc["p"].items() if k != "seed"}, **scenario_features(sc)}
    for pol in policy_set(pset):
        r = simulate(sc, pol)
        out[f"U:{pol.name}"] = round(r["U"], 4)
        if "minW" in r:
            out[f"minW:{pol.name}"] = round(r["minW"], 2)
        out[f"urg:{pol.name}"] = round(r["urgent_hit"], 4)
        out[f"drop:{pol.name}"] = r["dropped"]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    sw = sub.add_parser("sweep")
    sw.add_argument("-n", type=int, required=True)
    sw.add_argument("-o", required=True)
    sw.add_argument("--seed0", type=int, default=0)
    sw.add_argument("--set", default="core")
    sw.add_argument("--fix", default="", help="k=v,k=v fixed scenario parameters")
    sw.add_argument("-j", type=int, default=8)
    gr = sub.add_parser("grid")
    gr.add_argument("-o", required=True)
    gr.add_argument("--set", default="final")
    gr.add_argument("-j", type=int, default=8)
    gr.add_argument("--fix", default="")
    gr.add_argument("--seed0", type=int, default=200000)
    gr.add_argument("--long", action="store_true", help="add routine deadlines 2 h and 6 h")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "grid":
        return grid(a)
    fixed = {}
    for kv in filter(None, a.fix.split(",")):
        k, v = kv.split("=")
        fixed[k] = v if k == "content_var" else float(v)
    import multiprocessing as mp
    jobs = [(a.seed0 + i, fixed, a.set) for i in range(a.n)]
    os.makedirs(os.path.dirname(os.path.abspath(a.o)), exist_ok=True)
    t0 = time.time()
    rows = []
    with mp.Pool(a.j) as pool:
        for k, r in enumerate(pool.imap_unordered(run_job, jobs, chunksize=4)):
            rows.append(r)
            if (k + 1) % 200 == 0:
                print(f"  {k + 1}/{a.n}  {time.time() - t0:.0f} s", flush=True)
    rows.sort(key=lambda r: r["seed"])
    cols = list(rows[0])
    with open(a.o, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader(); w.writerows(rows)
    print(f"wrote {a.o} ({len(rows)} scenarios, {time.time() - t0:.0f} s)")
    return 0


def grid(a):
    """Regime map: routine deadline x burst length x frame period x content."""
    import multiprocessing as mp, itertools
    fixed = {}
    for kv in filter(None, a.fix.split(",")):
        k, v = kv.split("=")
        fixed[k] = float(v)
    contents = {"cloudy": (0.3, 0.2), "average": (0.71, 0.33), "clear": (1.0, 1.0)}
    jobs = []
    for i, (slo, L_, per, (cn, (w, c)), sd) in enumerate(itertools.product(
            (60, 120, 300, 600, 1200, 2400) + ((7200, 21600) if a.long else ()),
            (5, 20, 60, 150, 400), (2.2, 3.34, 4.5, 6.68, 10.0),
            contents.items(), (1, 2))):
        f = dict(slo_r=slo, burst_mean=L_, burst_cv=0.0, period=per, gap=600.0, urgent=0.1,
                 water=w, clear=c, content_var="const", hi_scale=1.0, frames_target=600, **fixed)
        jobs.append((a.seed0 + i, f, a.set, cn))
    t0 = time.time()
    with mp.Pool(a.j) as pool:
        rows = list(pool.imap_unordered(run_grid_job, jobs, chunksize=2))
    rows.sort(key=lambda r: r["seed"])
    with open(a.o, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print(f"wrote {a.o} ({len(rows)} cells, {time.time() - t0:.0f} s)")
    return 0


def run_grid_job(args):
    seed, fixed, pset, cname = args
    r = run_job((seed, fixed, pset))
    r["content"] = cname
    return r


def selftest():
    """Same trace, same frames, same policies -> same U as sched_sim."""
    content, sets = S.build_content()
    base = base_cost()
    ok = True
    for tr, cset in (("main-s1", "keepall"), ("main2x-s1", "matched"), ("main2x-s1", "keepall")):
        arr = S.read_trace(os.path.join(ROOT, f"experiments/traces/{tr}.trace"))
        frames = sets[cset]
        rows = []
        for i, (a_, s_, c_) in enumerate(arr):
            ci = content[frames[i % len(frames)]]
            ns = int(round(ci["nseg"]["low"]))
            rows.append((a_, s_, c_, ns, int(round(ns * ci["nenc_ratio"]["low"]))))
        sc = {"arrivals": rows, "slo_r": 300.0, "cost": Cost(base), "over": 0.37, "ingest_s": 0.41,
              "ckpt": 0.5, "window": 8, "p": {"period": 3.34}}
        for name, pol, sk in (("static-low", Static("low"), dict(policy="static-low", gran="batch")),
                              ("static-high", Static("high"), dict(policy="static-high", gran="batch")),
                              ("ladder", Ladder(), dict(policy="ladder", gran="stage"))):
            mine = simulate(sc, pol)["U"]
            ref = S.summarise(*S.simulate(arr, frames, content, base, sk["policy"], sk["gran"], 0.37, 0.5,
                                          ingest_s=0.41, q1=12, q2=20)[:2])["all"]["U"]
            flag = abs(mine - ref) < 0.03
            ok &= flag
            print(f"  {tr:10} {cset:8} {name:12} lab {mine:.3f}  sched_sim {ref:.3f}  {'ok' if flag else 'DIFF'}")
    print("OK" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
