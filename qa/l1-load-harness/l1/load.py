#!/usr/bin/env python3
"""H1 load.py: the L1 load driver. Completes the 750 pre-staged QAL drafts at the rate the governor allows, each draft at most
ONCE (no retry, no re-send, ever), until the plan is sent or a LOAD / SAFETY STOP.

  load.py plan-template --out FILE           deterministic 750-row plan (no network)
  load.py simulate [--cost N] --out DIR      whole driver + governor against the offline World model (no network)
  load.py live --plan BOUND.json --plan-sha SHA --state-dir DIR --confirm PHRASE [--approve-t P] [--approve-t2 P]
               --restore-cmd CMD             (Stage 4 only, separately approved)

Refusals before anything is sent: confirmation phrase; bound plan valid and its sha256 equal to --plan-sha; QAL allow-list (no
retired fixture, every draft in the plan); one-shot marker absent; T/T2 phrases exact; live gates (fire.py check_live_gates:
production Worker absent, QA Worker single gate-passed version with DRY_RUN "false", arming age, guard running, no manual-stop);
fresh sampler and failwatch files; drafts-open precheck for this plan sha no older than 30 min.
The marker fire-style `load-<competition>.done` is created with O_EXCL before the first mutation.
Delivery is confirmed only by a Shopify request id. Outcomes: SUCCESS | THROTTLED_NOT_EXECUTED (left OPEN, never retried) |
ERROR (-> LOAD STOP) | UNKNOWN (sent, no confirmed response -> LOAD STOP).
Exit codes: 0 plan complete; 2 refused before any mutation; 3 stopped (LOAD or SAFETY) after mutations began."""
import argparse, json, os, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import (CONFIRM_LIVE, APPROVE_T, APPROVE_T2, PREFIX, Refused, allowed_draft, canonical, plan_hash, plan_template,  # noqa: E402
                    scrub, validate_plan, write_json)
from governor import Governor, Signals  # noqa: E402
from shop import is_throttled, throttle_of  # noqa: E402

MUTATION = ("mutation CompleteL1Draft($id: ID!) { draftOrderComplete(id: $id) { draftOrder { id name status } "
            "userErrors { field message } } }")
TICK_S = 0.1
WORKERS = 12
DRIVER_BUCKET_FLOOR = 300     # the Stress Driver's own bucket: pause sending below this (never let it throttle)
PRECHECK_MAX_AGE_S = 1800


class Pacer:
    """Releases whole send tokens at `rate`/s. The accumulator is capped at one token, so a rate change or a pause never
    produces a catch-up burst."""

    def __init__(self):
        self.acc, self.t = 0.0, None

    def tokens(self, now, rate):
        if self.t is None:
            self.t = now
        dt, self.t = max(0.0, now - self.t), now
        if rate <= 0:
            self.acc = 0.0
            return 0
        self.acc += min(rate * dt, 1.0)          # at most one token's worth per call: a pause never turns into a burst
        n = int(self.acc + 1e-9)                 # float tolerance: 4 x 0.25 must release a token
        self.acc = max(0.0, self.acc - n)
        return n


def classify(status, headers, body):
    rid = (headers or {}).get("x-request-id")
    if not rid:
        return "UNKNOWN", rid
    if is_throttled(body):
        return "THROTTLED_NOT_EXECUTED", rid
    d = ((body or {}).get("data") or {}).get("draftOrderComplete") or {}
    if status == 200 and not (body or {}).get("errors") and not d.get("userErrors") and (d.get("draftOrder") or {}).get("status") == "COMPLETED":
        return "SUCCESS", rid
    return "ERROR", rid


class Evidence:
    def __init__(self, path, secrets=()):
        self.path, self.secrets, self.lock = path, list(secrets), threading.Lock()
        self.records, self.decisions = [], []

    def add(self, kind, obj):
        line = scrub(canonical({"kind": kind, **obj}), self.secrets)
        with self.lock:
            (self.records if kind == "request" else self.decisions).append(json.loads(line))
            if self.path:
                with open(self.path, "a") as f:
                    f.write(line + "\n")


class SyncExecutor:
    def submit(self, fn, *a):
        fn(*a)

    def join(self):
        pass


class ThreadExecutor:
    def __init__(self, n=WORKERS):
        self.pool, self.futs = ThreadPoolExecutor(max_workers=n), []

    def submit(self, fn, *a):
        self.futs.append(self.pool.submit(fn, *a))

    def join(self):
        for f in self.futs:
            f.result()
        self.pool.shutdown(wait=True)


def check_offline_gates(plan, expected_sha, confirm, marker, approve_t=None, approve_t2=None):
    """Everything that can refuse without a network call. Raises Refused."""
    if confirm != CONFIRM_LIVE:
        raise Refused("confirmation phrase missing or wrong")
    probs = validate_plan(plan, bound=True)
    if probs:
        raise Refused("plan invalid: " + "; ".join(probs[:5]))
    if plan_hash(plan) != expected_sha:
        raise Refused("plan sha256 differs from the approved one")
    if os.path.exists(marker):
        raise Refused(f"one-shot marker exists: {marker}")
    if approve_t not in (None, APPROVE_T):
        raise Refused("T approval phrase is wrong")
    if approve_t2 not in (None, APPROVE_T2):
        raise Refused("T2 approval phrase is wrong")
    if approve_t2 and not approve_t:
        raise Refused("T2 requires T")


def create_marker(marker, plan_sha, now):
    fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(canonical({"plan_sha256": plan_sha, "created": now}) + "\n")


def run(plan, client, gov, signals, clock, marker, evidence, executor=None, restore_cb=None, plan_sha=None, max_s=7200):
    """The driver loop. Each plan row is dispatched at most once; nothing is ever re-queued."""
    executor = executor or SyncExecutor()
    rows = list(plan["rows"])
    flags = {"unknown": False, "error": False, "driver_bucket": None}
    arrivals = []
    lock = threading.Lock()
    create_marker(marker, plan_sha or plan_hash(plan), clock.now())
    evidence.add("decision", {"t": clock.now(), "event": "marker created", "marker": os.path.basename(marker)})

    def send_one(row, phase):
        rec = {"index": row["index"], "draft_id": row["draft_id"], "qty": row["qty"], "tag": row["tag"], "phase": phase,
               "t_send": clock.now()}
        if not allowed_draft(plan, row["draft_id"]):
            rec.update(outcome="REFUSED_NOT_IN_PLAN")
            evidence.add("request", rec)
            return
        try:
            st, h, body = client.post(MUTATION, {"id": row["draft_id"]})
        except Exception as e:
            rec.update(outcome="UNKNOWN", error=type(e).__name__, t_done=clock.now())
            with lock:
                flags["unknown"] = True
            evidence.add("request", rec)
            return
        outcome, rid = classify(st, h, body)
        thr = throttle_of(body)
        rec.update(outcome=outcome, http_status=st, shopify_request_id=rid, delivery_confirmed=bool(rid), t_done=clock.now(),
                   throttle=thr, user_errors=(((body or {}).get("data") or {}).get("draftOrderComplete") or {}).get("userErrors"),
                   errors=[(e or {}).get("message", "")[:120] for e in (body or {}).get("errors") or []])
        with lock:
            if outcome == "UNKNOWN":
                flags["unknown"] = True
            elif outcome == "ERROR":
                flags["error"] = True
            if outcome == "SUCCESS":
                arrivals.append(rec["t_done"])
            if thr.get("available") is not None:
                flags["driver_bucket"] = thr["available"]
        evidence.add("request", rec)

    pacer, idx, last_key, t0 = Pacer(), 0, None, clock.now()
    restore_done = False
    while True:
        now = clock.now()
        s = signals.read(now, arrivals) if hasattr(signals, "read") else signals(now, arrivals)
        with lock:
            s.unknown_outcome = s.unknown_outcome or flags["unknown"]
            s.completion_error = s.completion_error or flags["error"]
        d = gov.update(now, s)
        key = (d["phase"], d["state"], d["rate"], d["r_star"])
        if key != last_key:
            evidence.add("decision", d)
            last_key = key
        if "RESTORE_DRY_RUN" in d["actions"] and not restore_done:
            ok = bool(restore_cb and restore_cb())
            evidence.add("decision", {"t": now, "event": "RESTORE_DRY_RUN requested", "ok": ok})
            if ok:
                gov.ack_restore()
                restore_done = True
        if d["state"] != "RUNNING" or d["phase"] in ("E", "DONE") or idx >= len(rows):
            break
        if now - t0 > max_s:
            evidence.add("decision", {"t": now, "event": "driver time limit reached; stopping (no re-send)"})
            break
        n = pacer.tokens(now, d["rate"])
        if flags["driver_bucket"] is not None and flags["driver_bucket"] < DRIVER_BUCKET_FLOOR:
            n = 0
        for _ in range(n):
            if idx >= len(rows):
                break
            row = rows[idx]
            idx += 1
            gov.on_sent(1)
            executor.submit(send_one, row, d["phase"])
        clock.sleep(TICK_S)
    executor.join()
    outcomes = {}
    for r in evidence.records:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    summary = {"dispatched": idx, "planned": len(rows), "outcomes": outcomes, "governor": gov.summary(),
               "never_sent": [r["index"] for r in rows[idx:]], "restore_requested": restore_done}
    evidence.add("decision", {"t": clock.now(), "event": "summary", **summary})
    return summary


# ---- live wiring (Stage 4 only) -------------------------------------------------------------------------------------------------
class FileSignals:
    """Live signals from the state directory, written by sampler.py and guardl.py. Missing or stale files never read as healthy:
    a stale guard heartbeat is a SAFETY reason; a missing sampler sample leaves the bucket stale (governor LOAD STOP)."""

    def __init__(self, state_dir, guard_stale_s=10.0):
        self.d, self.guard_stale_s = state_dir, guard_stale_s

    def _json(self, name, default):
        try:
            with open(os.path.join(self.d, name)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def read(self, now, arrivals):
        bucket = []
        try:
            with open(os.path.join(self.d, "sampler.jsonl")) as f:
                for line in f.readlines()[-60:]:
                    r = json.loads(line)
                    if r.get("available") is not None:
                        bucket.append((r["ts"], r["available"]))
        except (OSError, ValueError):
            pass
        g = self._json("guardl.json", {})
        safety = list(g.get("safety") or [])
        if not g or now - float(g.get("ts", 0)) > self.guard_stale_s:
            safety.append("guardl heartbeat missing or stale")
        if os.path.exists(os.path.join(self.d, "manual-stop")):
            safety.append("manual-stop present")
        load = list(g.get("load") or [])
        if os.path.exists(os.path.join(self.d, "load-stop")):
            load.append("load-stop file present")
        fw = self._json("failwatch.json", {})
        return Signals(bucket=bucket, throttled=g.get("throttled") or [], wall_ms_max=g.get("wall_ms_max") or 0,
                       fw_level=fw.get("level", "NONE"), arr_shifted30=sum(1 for t in arrivals if now - 35 <= t <= now - 5),
                       done30=sum(1 for t in g.get("done_ts") or [] if now - 30 <= t <= now), safety=safety, load=load,
                       drained=bool(g.get("drained")))


def live(a):
    import importlib.util  # noqa: F401
    from shop import Client, load_fire
    if a.confirm != CONFIRM_LIVE:
        raise Refused("confirmation phrase missing or wrong")       # before reading or touching anything
    plan = json.load(open(a.plan))
    marker = os.path.join(a.state_dir, f"load-{plan.get('competition_id')}.done")
    check_offline_gates(plan, a.plan_sha, a.confirm, marker, a.approve_t, a.approve_t2)
    pre = json.load(open(os.path.join(a.state_dir, "drafts-open.json")))
    if pre.get("plan_sha256") != a.plan_sha or time.time() - pre.get("ts", 0) > PRECHECK_MAX_AGE_S or pre.get("open") != len(plan["rows"]):
        raise Refused("drafts-open precheck missing, stale, for another plan, or not all drafts OPEN")
    fire = load_fire()
    gates = fire.check_live_gates(fire.CloudflareTransport(), os.environ, a.state_dir, time.time())
    if gates:
        raise Refused("live gates: " + "; ".join(gates))
    client = Client("stress_driver", {MUTATION})
    if not client.scopes() <= {"write_draft_orders", "read_draft_orders"} or "write_draft_orders" not in client.scopes():
        raise Refused(f"Stress Driver token scopes {sorted(client.scopes())} are not exactly write_draft_orders (+implied read)")
    gov = Governor(a.approve_t, a.approve_t2)
    ev = Evidence(os.path.join(a.state_dir, "load-evidence.jsonl"), client.secrets)

    def restore():
        with open(os.path.join(a.state_dir, "safety-stop"), "a") as f:
            f.write(f"{time.time():.3f} load.py SAFETY -> restore\n")
        return subprocess.run(a.restore_cmd, shell=True).returncode == 0

    class RealClock:
        now = staticmethod(time.time)
        sleep = staticmethod(time.sleep)
    s = run(plan, client, gov, FileSignals(a.state_dir), RealClock(), marker, ev, ThreadExecutor(), restore, a.plan_sha)
    write_json(os.path.join(a.state_dir, "load-summary.json"), s)
    return 0 if s["dispatched"] == len(plan["rows"]) and gov.state in ("RUNNING", "DONE") else 3


def simulate(a):
    from sim import FakeClock, SimClient, World
    clock = FakeClock()
    world = World(clock, cost=a.cost)
    plan = plan_template()
    plan["competition_id"] = "999999999"
    for r in plan["rows"]:
        r["draft_id"] = f"gid://shopify/DraftOrder/{r['index']}"
    os.makedirs(a.out, exist_ok=True)
    marker = os.path.join(a.out, "load-sim.done")
    if os.path.exists(marker):
        os.remove(marker)       # simulation only: a fresh model run each time; the live marker is never touched here
    ev = Evidence(os.path.join(a.out, "sim-evidence.jsonl"))
    gov = Governor()
    s = run(plan, SimClient(world), gov, lambda now, arr: world.signals(now, arr), clock, marker, ev)
    while not world.signals(clock.now()).drained and clock.now() < 1_800_000_000 + 7200:
        clock.sleep(1)
    out = {"summary": s, "min_bucket": world.min_bucket, "throttled": len(world.throttled), "failwatch": world.fw.state()["level"],
           "completed": len(world.completed)}
    write_json(os.path.join(a.out, "sim-summary.json"), out)
    print(canonical({k: out[k] for k in ("min_bucket", "throttled", "failwatch", "completed")}))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="load.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("plan-template"); t.add_argument("--out", required=True)
    m = sub.add_parser("simulate"); m.add_argument("--out", required=True); m.add_argument("--cost", type=float, default=28.0)
    lv = sub.add_parser("live")
    for k in ("--plan", "--plan-sha", "--state-dir", "--confirm", "--restore-cmd"):
        lv.add_argument(k, required=True)
    lv.add_argument("--approve-t"); lv.add_argument("--approve-t2")
    a = p.parse_args(argv)
    try:
        if a.cmd == "plan-template":
            pl = plan_template()
            write_json(a.out, pl)
            print(f"plan template: {pl['orders']} orders, {pl['units']} units, sha256 {plan_hash(pl)}")
            return 0
        if a.cmd == "simulate":
            return simulate(a)
        return live(a)
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
