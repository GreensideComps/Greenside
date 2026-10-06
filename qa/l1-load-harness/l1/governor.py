"""H1 governor: the L1 phase and rate state machine (pure logic; time and signals are inputs).

Default path: A (baseline) -> B (staircase) -> C (sustain at r*) -> E (drain). T and T2 exist only behind their own approval
phrases and never run by default.

Each tick the caller passes `now` and a Signals snapshot. The governor returns a Decision: the phase, the target arrival rate
(never above HARD_CEILING_PER_S), the run state (RUNNING / LOAD_STOP / SAFETY_STOP / DONE) and actions (RESTORE_DRY_RUN). States
only ever rise. Rules (L1 v2 section 3):
  WARNING   bucket < 1200, or projected below 1200 within 10 s | THROTTLED retry outside T | Worker wall > 3 s
            -> in A: stay at the baseline rate; in B: stop climbing, r* = last passing step (else the baseline rate), go to C;
               in C: r* drops one step (never below the baseline rate)
  LOAD STOP failwatch LOAD_STOP | unknown completion outcome | non-throttle completion error | bucket < 600 outside T/T2 |
            >= 3 THROTTLED in 10 s outside T/T2 | bucket telemetry stale -> rate 0, no new arrivals, phase E (window stays live)
  SAFETY    failwatch SAFETY_STOP | any external safety reason -> rate 0, action RESTORE_DRY_RUN on every tick until acknowledged
A staircase step is complete when its quota (rate x 30 s) has been sent, i.e. 30 s at that rate; it passes when, over the step:
bucket slope >= -3 pts/s, minimum bucket >= 1200, no THROTTLED, and completions >= 95% of (latency-shifted) arrivals.
Default staircase (amendment of 6 Oct 2026): 1.0..4.0/s by 0.5, 30 s each, 525 orders if every step passes; escalation stops after a
fully judged 4.0/s step (4.5 and 5.0/s are never entered). Population: A 50 + B up to 525 + C the rest; T only when approved AND
its 40 orders are budgeted before execution (t_budget), which reduces C explicitly."""
from common import (BUCKET_LOAD_STOP, BUCKET_WARN, HARD_CEILING_PER_S, N_ORDERS, PHASE_A_ORDERS, PHASE_A_RATE, PHASE_B_MAX_ORDERS,
                    PHASE_T_ORDERS, PHASE_T_RATE, STAIRCASE, STEP_QUOTAS, STEP_SECONDS, THROTTLE_LOAD_STOP_N,
                    THROTTLE_LOAD_STOP_WINDOW_S, population_budget, stability_statement,
                    WALL_WARN_MS, Refused, APPROVE_T, APPROVE_T2)

STATES = ("RUNNING", "LOAD_STOP", "SAFETY_STOP", "DONE")
SLOPE_PASS = -3.0           # pts/s over the step's last 30 s
COMPLETION_PASS = 0.95
PROJECT_S = 10.0
MIN_JUDGE_S = 0.8 * STEP_SECONDS   # defensive cap branch only: a cut step shorter than this is INCOMPLETE
C_DROP_COOLDOWN_S = 15.0    # in C, a persisting WARNING lowers r* at most one step per 15 s (no collapse on every tick)
BUCKET_STALE_S = 8.0        # sampler runs every 2 s; four missed samples = blind -> LOAD STOP


class Signals:
    """One snapshot of everything the governor reads. bucket: [(ts, available)] newest last; throttled: [ts] THROTTLED retry log
    lines; wall_ms_max: max Worker wall time in the last 10 s; fw_level: failwatch level; arr_shifted30 / done30: arrivals in
    [t-35, t-5] and completed orders in [t-30, t]; unknown_outcome / completion_error: driver flags; safety / load: external
    SAFETY or LOAD STOP reasons (guard, manual stop, stale guard); drained: guard says every sent order is allocated."""

    def __init__(self, bucket=(), throttled=(), wall_ms_max=0, fw_level="NONE", arr_shifted30=0, done30=0,
                 unknown_outcome=False, completion_error=False, safety=(), drained=False, load=()):
        self.bucket, self.throttled, self.wall_ms_max = list(bucket), list(throttled), wall_ms_max
        self.fw_level, self.arr_shifted30, self.done30 = fw_level, arr_shifted30, done30
        self.unknown_outcome, self.completion_error = unknown_outcome, completion_error
        self.safety, self.drained, self.load = list(safety), drained, list(load)


def slope(samples, t0, t1):
    pts = [(t, v) for t, v in samples if t0 <= t <= t1]
    if len(pts) < 2:
        return 0.0
    n = len(pts)
    mt = sum(t for t, _ in pts) / n
    mv = sum(v for _, v in pts) / n
    den = sum((t - mt) ** 2 for t, _ in pts)
    return 0.0 if den == 0 else sum((t - mt) * (v - mv) for t, v in pts) / den


class Governor:
    def __init__(self, approve_t=None, approve_t2=None, n_orders=N_ORDERS, t_budget=0):
        if approve_t not in (None, APPROVE_T):
            raise Refused("T approval phrase is wrong")
        if approve_t2 not in (None, APPROVE_T2):
            raise Refused("T2 approval phrase is wrong")
        if approve_t2 and not approve_t:
            raise Refused("T2 requires T to be approved as well")
        self.t_on, self.t2_on = approve_t == APPROVE_T, approve_t2 == APPROVE_T2
        if self.t_on and t_budget != PHASE_T_ORDERS:
            raise Refused(f"T is approved but its {PHASE_T_ORDERS}-order population was not budgeted before execution")
        if not self.t_on and t_budget:
            raise Refused("a T budget was given but T is not approved")
        self.budget = population_budget(n_orders, t_budget)      # refuses if the phases cannot fit in the plan
        self.n_orders = n_orders
        self.reserve_t = t_budget
        self.phase, self.state = "A", "RUNNING"
        self.sent = {"A": 0, "B": 0, "C": 0, "T": 0, "T2": 0}
        self.step, self.step_start, self.step_base, self.step_log = -1, None, 0, []
        self.r_star, self.r_star_bracketed = None, False
        self.last_pass_rate = None
        self.log, self.warnings = [], []
        self.restore_acked = False
        self.last_c_drop = float("-inf")
        self.bracket = None
        self.started = None

    # -- bookkeeping from the driver
    def total_sent(self):
        return sum(self.sent.values())

    def on_sent(self, n=1):
        self.sent[self.phase] += n

    def ack_restore(self):
        self.restore_acked = True

    def _set_state(self, st, why, now):
        # SAFETY always wins (even after DONE); LOAD_STOP only interrupts a RUNNING window
        if (st == "SAFETY_STOP" and self.state != "SAFETY_STOP") or (st == "LOAD_STOP" and self.state == "RUNNING"):
            self.state = st
            self.log.append({"t": now, "event": st, "why": why, "phase": self.phase})
            if st in ("LOAD_STOP", "SAFETY_STOP") and self.phase not in ("E", "DONE"):
                self.phase = "E"

    def _go(self, phase, now, why):
        self.log.append({"t": now, "event": f"phase {self.phase}->{phase}", "why": why})
        self.phase = phase

    def _enter_c(self, rate, now, why, bracketed):
        self.r_star, self.r_star_bracketed = min(rate, HARD_CEILING_PER_S), bracketed
        # the stability-point bracket is fixed here and never changed by later safety drops of r* in C
        self.bracket = {"low": self.last_pass_rate, "high": STAIRCASE[self.step] if bracketed and self.step >= 0 else
                        (PHASE_A_RATE if bracketed else None), "bracketed": bracketed, "why": why}
        self.bracket["statement"] = stability_statement(self.bracket)
        self._go("C", now, f"{why}; r*={self.r_star}/s")

    # -- one tick
    def update(self, now, s):
        if self.started is None:
            self.started = now
        last = s.bucket[-1] if s.bucket else None
        in_t = self.phase in ("T", "T2")
        # SAFETY: always first, from any phase
        if s.fw_level == "SAFETY_STOP" or s.safety:
            self._set_state("SAFETY_STOP", "; ".join(s.safety) or "failwatch SAFETY_STOP", now)
        if self.state == "RUNNING":
            recent_thr = [t for t in s.throttled if now - THROTTLE_LOAD_STOP_WINDOW_S <= t <= now]
            if s.fw_level in ("LOAD_STOP",):
                self._set_state("LOAD_STOP", "failwatch LOAD_STOP (a failed delivery)", now)
            elif s.load:
                self._set_state("LOAD_STOP", "; ".join(s.load), now)
            elif s.unknown_outcome:
                self._set_state("LOAD_STOP", "a completion with an unknown outcome", now)
            elif s.completion_error:
                self._set_state("LOAD_STOP", "a non-throttle completion error", now)
            elif last is None or now - last[0] > BUCKET_STALE_S:
                self._set_state("LOAD_STOP", "allocator bucket telemetry missing or stale", now)
            elif not in_t and last[1] < BUCKET_LOAD_STOP:
                self._set_state("LOAD_STOP", f"allocator bucket {last[1]} < {BUCKET_LOAD_STOP}", now)
            elif not in_t and len(recent_thr) >= THROTTLE_LOAD_STOP_N:
                self._set_state("LOAD_STOP", f"{len(recent_thr)} THROTTLED in {THROTTLE_LOAD_STOP_WINDOW_S:.0f}s", now)
        if self.state == "RUNNING":
            self._advance(now, s, last, in_t)
        if self.phase == "E" and s.drained and self.state != "SAFETY_STOP":
            self._go("DONE", now, "drained")
            if self.state == "RUNNING":
                self.state = "DONE"
        rate = self._rate() if self.state == "RUNNING" else 0.0
        rate = min(rate, HARD_CEILING_PER_S)
        assert 0.0 <= rate <= HARD_CEILING_PER_S
        actions = ["RESTORE_DRY_RUN"] if self.state == "SAFETY_STOP" and not self.restore_acked else []
        return {"t": now, "phase": self.phase, "state": self.state, "rate": rate, "r_star": self.r_star,
                "step": self.step, "actions": actions, "sent": self.total_sent()}

    def _warning(self, now, s, last, in_t):
        why = []
        if last[1] < BUCKET_WARN:
            why.append(f"bucket {last[1]} < {BUCKET_WARN}")
        sl = slope(s.bucket, now - 10, now)
        if sl < 0 and last[1] + sl * PROJECT_S < BUCKET_WARN:
            why.append(f"bucket projected {last[1] + sl * PROJECT_S:.0f} in {PROJECT_S:.0f}s (slope {sl:.1f}/s)")
        if not in_t and any(now - 10 <= t <= now for t in s.throttled):
            why.append("THROTTLED retry")
        if s.wall_ms_max > WALL_WARN_MS:
            why.append(f"Worker wall {s.wall_ms_max}ms > {WALL_WARN_MS}ms")
        return why

    def _advance(self, now, s, last, in_t):
        why = self._warning(now, s, last, in_t)
        if why:
            self.warnings.append({"t": now, "phase": self.phase, "why": why})
        remaining = self.n_orders - self.total_sent()
        if self.phase == "A":
            if self.sent["A"] >= PHASE_A_ORDERS:
                if why:
                    self._enter_c(PHASE_A_RATE, now, "WARNING at the end of A: " + "; ".join(why), True)
                else:
                    self._start_step(0, now)
        elif self.phase == "B":
            if why:
                self.step_log.append({"step": self.step, "rate": STAIRCASE[self.step], "result": "WARNING", "why": why, "t": now})
                self._enter_c(self.last_pass_rate or PHASE_A_RATE, now, "WARNING in B: " + "; ".join(why), True)
            elif self.sent["B"] - self.step_base >= STEP_QUOTAS[self.step]:
                ok = self._step_passes(now, s)
                self.step_log.append({"step": self.step, "rate": STAIRCASE[self.step], "result": "PASS" if ok else "FAIL", "t": now})
                if ok:
                    self.last_pass_rate = STAIRCASE[self.step]
                    if self.step + 1 < len(STAIRCASE):
                        self._start_step(self.step + 1, now)
                    else:
                        self._enter_c(self.last_pass_rate, now, f"top step {STAIRCASE[-1]}/s passed: escalation stops "
                                      "(upper stability boundary not bracketed by L1)", False)
                else:
                    self._enter_c(self.last_pass_rate or PHASE_A_RATE, now, f"step {STAIRCASE[self.step]}/s failed", True)
            elif self.sent["B"] >= PHASE_B_MAX_ORDERS:   # defensive: unreachable while quotas sum to the cap
                # A step cut short by the cap is judged only if it ran long enough for its 30 s window; otherwise it is
                # recorded as INCOMPLETE and never promoted to r*.
                if now - self.step_start >= MIN_JUDGE_S:
                    ok = self._step_passes(now, s)
                    result = "PASS" if ok else "FAIL"
                else:
                    ok, result = None, "INCOMPLETE"
                self.step_log.append({"step": self.step, "rate": STAIRCASE[self.step], "result": result, "t": now,
                                      "note": "B order cap reached"})
                if ok:
                    self.last_pass_rate = STAIRCASE[self.step]
                self._enter_c(self.last_pass_rate or PHASE_A_RATE, now, "B order cap reached", ok is False)
        elif self.phase == "C":
            if why and now - self.last_c_drop >= C_DROP_COOLDOWN_S:
                lower = max(PHASE_A_RATE, (self.r_star or PHASE_A_RATE) - 0.5)
                self.last_c_drop = now
                if lower < (self.r_star or PHASE_A_RATE):
                    self.log.append({"t": now, "event": f"r* {self.r_star}->{lower}", "why": "WARNING in C: " + "; ".join(why)})
                    self.r_star = lower
            if remaining - self.reserve_t <= 0:
                self._go("T" if self.t_on else "E", now, "C complete")
        elif self.phase == "T":
            if self.sent["T"] >= (PHASE_T_ORDERS // 2 if self.t2_on else PHASE_T_ORDERS) or remaining <= 0:
                self._go("T2" if self.t2_on else "E", now, "T complete")
        elif self.phase == "T2":
            if remaining <= 0:
                self._go("E", now, "T2 complete")
        if self.phase in ("A", "B", "C", "T", "T2") and self.total_sent() >= self.n_orders:
            self._go("E", now, "all planned orders sent")

    def _start_step(self, k, now):
        self.step, self.step_start, self.step_base = k, now, self.sent["B"]
        if self.phase != "B":
            self._go("B", now, f"step {STAIRCASE[k]}/s")
        else:
            self.log.append({"t": now, "event": f"step {STAIRCASE[k]}/s", "why": "previous step passed"})

    def _step_passes(self, now, s):
        t0 = max(self.step_start, now - 30)
        sl = slope(s.bucket, t0, now)
        mins = min((v for t, v in s.bucket if self.step_start <= t <= now), default=0)
        thr = [t for t in s.throttled if self.step_start <= t <= now]
        done_ok = s.arr_shifted30 == 0 or s.done30 >= COMPLETION_PASS * s.arr_shifted30
        return sl >= SLOPE_PASS and mins >= BUCKET_WARN and not thr and done_ok

    def _rate(self):
        if self.phase == "A":
            return PHASE_A_RATE
        if self.phase == "B":
            return STAIRCASE[self.step]
        if self.phase == "C":
            return self.r_star or PHASE_A_RATE
        if self.phase in ("T", "T2"):
            return PHASE_T_RATE
        return 0.0

    def summary(self):
        return {"state": self.state, "phase": self.phase, "sent": dict(self.sent), "r_star": self.r_star,
                "r_star_bracketed": self.r_star_bracketed, "stability_bracket": self.bracket, "steps": list(self.step_log), "log": list(self.log),
                "warnings": list(self.warnings), "t_approved": self.t_on, "t2_approved": self.t2_on}
