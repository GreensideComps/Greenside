#!/usr/bin/env python3
"""H7 guardl.py: the L1 load-window guard (decision core + one-tick CLI). guardl.sh calls `guardl.py tick` once a second.

Decision core (pure, offline-tested): Guard.tick(now, inputs) -> {safety, load, actions, due, drained, verdict}.
SAFETY (-> restore to DRY_RUN=true at once, retried until verified):
  failwatch SAFETY_STOP | webhook subscriptions changed or missing | D1 allow-list deviation (any change outside L1, any L1
  allocation not explained by a completed plan draft, any release, integrity counter) | production Worker present | DRY_RUN
  changed unexpectedly or missing | QA Worker off the gate-passed version | QA Worker unverified for > 30 s (missing, failed or
  stale reading) | manual-stop | load.py SAFETY (safety-stop file)
LOAD STOP (driver stops sending; window stays live; drain continues):
  failwatch LOAD_STOP (a failed delivery seen by a tail OR by Workers Logs; an incomplete Workers Logs window) | Workers Logs
  coverage missing, gapped or stale (> 150 s) | subscription check stale (> 90 s) | D1 count or allow-list overdue
DRAIN (after the driver stops): complete when D1 holds exactly the units of every completed draft -> planned restore;
  30 min after the last order -> deadline restore; no allocation progress for 10 min -> no-progress restore.
Schedules: D1 COUNT every 30 s; full allow-list every 5 min; subscriptions via the sampler every 30 s.
Restore is fail-safe: once requested it is requested on EVERY tick until DRY_RUN "true" is verified on the restored version; a
failed restore command is retried (guardl.sh, every 10 s) and raises an alarm file; it is never abandoned."""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import (ALLOWLIST_EVERY_S, D1_COUNT_EVERY_S, DRAIN_DEADLINE_S, NO_PROGRESS_S, SUBSCRIPTION_EVERY_S,  # noqa: E402
                    canonical, read_json, write_json)
from failwatch import FailWatch  # noqa: E402

SUBS_STALE_S = 3 * SUBSCRIPTION_EVERY_S
D1_OVERDUE_S = 3 * D1_COUNT_EVERY_S
ALLOWLIST_OVERDUE_S = 2 * ALLOWLIST_EVERY_S
# Stage 4 wiring (new checks; no existing threshold changed). livewin.py polls; the guard judges freshness and content.
WORKER_POLL_S = 10          # livewin.py poll-worker: one QA Worker reading (3 read-only GETs) every 10 s
WORKER_STALE_S = 30         # while live, no fresh good reading for > 30 s (3 polls) -> SAFETY (the Worker is unverified)
WL_POLL_S = 30              # livewin.py poll-wl: one Workers Logs window every 30 s ...
WL_LAG_S = 60               # ... ending 60 s in the past (measured ingestion delay 8.5-28.4 s; evidence.py waits 60 s)
WL_STALE_S = 150            # while live, Workers Logs coverage ending > 150 s ago (lag + poll + headroom) -> LOAD STOP
INTEGRITY_ZERO = ("dup_events", "audit_gaps", "ledger_drift", "orphans", "entry_order_mismatch", "dup_issue", "multi_held",
                  "dup_release", "dup_return", "freeze_audit", "bad_events")


# ---- tail parsing ------------------------------------------------------------------------------------------------------------
def tail_objects(text):
    dec, i, out = json.JSONDecoder(), 0, []
    while True:
        j = text.find("{", i)
        if j < 0:
            return out
        try:
            o, e = dec.raw_decode(text, j)
            i = e
        except ValueError:
            i = j + 1
            continue
        if isinstance(o, dict) and "eventTimestamp" in o:
            out.append(o)


def logs_of(o):
    out = []
    for line in o.get("logs") or []:
        for m in line.get("message") or []:
            try:
                out.append(json.loads(m))
            except Exception:
                out.append(m)
    return out


def tail_events(o):
    """One tail object -> (webhook invocation event or None, [THROTTLED retry timestamps])."""
    ts = o["eventTimestamp"] / 1000.0
    lg = logs_of(o)
    thr = [ts for m in lg if isinstance(m, dict) and m.get("event") == "shopify_request_retry" and m.get("error_kind") == "THROTTLED"]
    req = (o.get("event") or {}).get("request") or {}
    if "/webhooks/" not in (req.get("url") or ""):
        return None, thr
    h = req.get("headers") or {}
    st = ((o.get("event") or {}).get("response") or {}).get("status")
    if o.get("outcome") != "ok" or o.get("exceptions"):
        st = st if isinstance(st, int) and st >= 300 else None
    proc = [m for m in lg if isinstance(m, dict) and m.get("event") == "webhook_processed"]
    og = next((m.get("order_gid") for m in lg if isinstance(m, dict) and m.get("order_gid")), None)
    return {"ts": ts, "topic": h.get("x-shopify-topic"), "webhook_id": h.get("x-shopify-webhook-id"), "status": st,
            "wall_ms": o.get("wallTime"), "request_id": h.get("cf-ray"), "order": og,
            "processed": bool(proc) and isinstance(st, int) and 200 <= st < 300,
            "dry_run": any(isinstance(m, dict) and m.get("event") == "dry_run_webhook" for m in lg)}, thr


def wl_from_obsq(r):
    """b3obs/obsq.fetch_window result -> failwatch input. Converted invocations are tail-shaped, so the same parser applies."""
    evs = []
    for o in r.get("invocations") or []:
        ev, _ = tail_events(o)
        if ev:
            evs.append(ev)
    return {"events": evs, "complete": bool(r.get("complete")), "records": r.get("records"), "pages": r.get("pages")}


# ---- D1 allow-list (SW4C_SNAP form, b3stress/snap.sql) ------------------------------------------------------------------------
def allowlist(ref, now, cid, completed_qtys, pre_max_order_number):
    """Deviations of the live QA D1 snapshot from the pre-run reference, given the L1 competition id, the quantities of the
    drafts the driver has COMPLETED (multiset), and the highest order number that existed before the run."""
    dev = []
    cid = str(cid)
    rc = {c["competition_id"]: c for c in ref.get("competitions") or []}
    nc = {c["competition_id"]: c for c in now.get("competitions") or []}
    if set(rc) != set(nc):
        dev.append(f"competition set changed: +{sorted(set(nc) - set(rc))} -{sorted(set(rc) - set(nc))}")
    for k in rc:
        if k in nc and rc[k] != nc[k]:
            dev.append(f"competition {k} changed")
    if (nc.get(cid) or {}).get("status") != "OPEN":
        dev.append("L1 competition is not OPEN")
    if ref.get("put_summary") != now.get("put_summary"):
        dev.append("PUT summary changed")
    re_ = {(e[0], e[1]): e for e in ref.get("entries") or []}
    ne = {(e[0], e[1]): e for e in now.get("entries") or []}
    if set(re_) != set(ne):
        dev.append("entry row set changed")
    for k, e in ne.items():
        if k[0] != cid and re_.get(k) != e:
            dev.append(f"non-L1 entry {e[2]} changed")
    l1_alloc = {a[0]: a for a in now.get("allocations") or [] if a[2] == cid}
    for k, e in ne.items():
        if k[0] == cid:
            if e[3] not in ("AVAILABLE", "ALLOCATED"):
                dev.append(f"L1 entry {e[2]} status {e[3]}")
            if e[3] == "ALLOCATED" and e[4] not in l1_alloc:
                dev.append(f"L1 entry {e[2]} held by unknown allocation")
            if e[8] > 1:
                dev.append(f"L1 entry {e[2]} issued {e[8]} times")
    ra = {a[0]: a for a in ref.get("allocations") or []}
    na = {a[0]: a for a in now.get("allocations") or []}
    for k, a in ra.items():
        if na.get(k) != a:
            dev.append(f"pre-existing allocation {k[:12]} changed or vanished")
    new = [a for k, a in na.items() if k not in ra]
    for a in new:
        if a[2] != cid:
            dev.append(f"new allocation on competition {a[2]} (not L1)")
        try:
            num = int(str(a[4]).lstrip("#"))
        except ValueError:
            num = -1
        if num <= pre_max_order_number:
            dev.append(f"allocation for pre-existing order {a[4]}")
        if a[23] not in ("ALLOCATED", "PARTIAL"):
            dev.append(f"L1 allocation {a[4]} status {a[23]}")
    pool = list(completed_qtys)
    for a in new:
        q = a[10]
        if q in pool:
            pool.remove(q)
        else:
            dev.append(f"L1 allocation {a[4]} (qty {q}) not explained by a completed plan draft")
    rev = {e[0]: e for e in ref.get("events") or []}
    for e in now.get("events") or []:
        if e[0] in rev:
            if rev[e[0]] != e:
                dev.append(f"pre-existing event {e[0]} changed")
        elif e[2] != cid or e[7] != "ALLOCATED":
            dev.append(f"new event {e[0]} {e[7]} on {e[2]}")
    if len(rev) != len([e for e in now.get("events") or [] if e[0] in rev]):
        dev.append("pre-existing events vanished")
    integ = now.get("integrity") or {}
    for k in INTEGRITY_ZERO:
        if integ.get(k) != 0:
            dev.append(f"integrity {k} = {integ.get(k)}")
    return dev


# ---- the guard -----------------------------------------------------------------------------------------------------------------
class Guard:
    def __init__(self, t2_approved=False, expected_units=None):
        self.fw = FailWatch(t2_approved)
        self.restore_reason, self.restore_verified = None, False
        self.last_d1, self.last_allow, self.last_subs = None, None, None
        self.allocated, self.last_progress_t = None, None
        self.throttled, self.walls, self.done_orders = [], [], {}
        self.expected_units = expected_units
        self.safety, self.load = [], []
        self.drained = False
        self.verdict = None
        self.t0, self.last_worker, self.wl_hi_ms = None, None, None

    def _safety(self, why):
        if why not in self.safety:
            self.safety.append(why)
        if self.restore_reason is None:
            self.restore_reason = "SAFETY: " + why

    def _load(self, why):
        if why not in self.load:
            self.load.append(why)

    def tick(self, now, inp):
        """inp: events [webhook events], throttled [ts], wl {events, complete}|None, sampler {ts, subs_ts, subs_ok}|None,
        d1_count {ts, allocated}|None, allowlist {ts, deviations}|None, worker {dry_run, production_present, restored_verified},
        manual_stop, load_safety_file, driver {started, done, last_sent_ts, completed_units}.
        Stage 4 wiring: worker is the newest livewin.py reading {ts, read_ok, production_present, dry_run, versions,
        expected_version}; wl is a list (or one) of livewin.py Workers Logs windows {from_ms, to_ms, complete, events}; wl_from_ms
        is where Workers Logs coverage must start. The guard is LIVE from its first tick until a restore is requested: the window
        opens at the gate (DRY_RUN=false), before the driver starts."""
        if self.t0 is None:
            self.t0 = now
        live_from_gate = self.restore_reason is None
        for ev in inp.get("events") or []:
            self.fw.observe(ev, "tail")
            if ev.get("wall_ms") is not None:
                self.walls.append((ev["ts"], ev["wall_ms"]))
            if ev.get("processed") and ev.get("order") and ev["order"] not in self.done_orders:
                self.done_orders[ev["order"]] = ev["ts"]
        self.throttled += list(inp.get("throttled") or [])
        wins = inp.get("wl") or []
        if self.wl_hi_ms is None and isinstance(inp.get("wl_from_ms"), int):
            self.wl_hi_ms = inp["wl_from_ms"] - 1
        for win in (wins if isinstance(wins, list) else [wins]):
            # every window goes through the existing failwatch: failures count, an incomplete window is a LOAD STOP
            self.fw.confirm_wl(win.get("events") or [], bool(win.get("complete")))
            if not win.get("complete"):
                continue
            lo, hi = win.get("from_ms"), win.get("to_ms")
            if not (isinstance(lo, int) and isinstance(hi, int) and lo <= hi):
                self._load("Workers Logs window malformed (no valid from_ms/to_ms)")
            elif self.wl_hi_ms is None or lo > self.wl_hi_ms + 1:
                self._load(f"Workers Logs coverage gap before {lo} (covered to {self.wl_hi_ms})")
            else:
                self.wl_hi_ms = max(self.wl_hi_ms, hi)
        if live_from_gate:
            if self.wl_hi_ms is None:
                self._load("Workers Logs coverage start unknown (wl_from_ms missing)")
            elif now * 1000 - self.wl_hi_ms > WL_STALE_S * 1000:
                self._load(f"Workers Logs missing or stale: coverage ends {now - self.wl_hi_ms / 1000:.0f}s ago (> {WL_STALE_S}s)")
        drv = inp.get("driver") or {}
        if drv.get("done") and drv.get("last_sent_ts") is not None:
            self.fw.note_load_stop(drv["last_sent_ts"])
        lvl = self.fw.level
        if lvl == "SAFETY_STOP":
            self._safety("failwatch: " + (self.fw.reasons[-1] if self.fw.reasons else "SAFETY_STOP"))
        elif lvl == "LOAD_STOP":
            self._load("failwatch: " + (self.fw.reasons[-1] if self.fw.reasons else "LOAD_STOP"))
        s = inp.get("sampler")
        if s and s.get("subs_ts") is not None:
            self.last_subs = s["subs_ts"]
            if s.get("subs_ok") is False:
                self._safety("webhook subscriptions changed or missing")
        started = drv.get("started")
        window = started is not None and self.restore_reason is None
        if window and now - (self.last_subs or started) > SUBS_STALE_S:
            self._load(f"subscription check stale (> {SUBS_STALE_S}s)")
        if inp.get("d1_count"):
            c = inp["d1_count"]
            self.last_d1 = c["ts"]
            if self.allocated is None or c["allocated"] != self.allocated:
                self.allocated, self.last_progress_t = c["allocated"], c["ts"]
        if inp.get("allowlist"):
            self.last_allow = inp["allowlist"]["ts"]
            for d in inp["allowlist"].get("deviations") or []:
                self._safety("D1 allow-list: " + d)
        if window and now - (self.last_d1 or started) > D1_OVERDUE_S:
            self._load("D1 count overdue")
        if window and now - (self.last_allow or started) > ALLOWLIST_OVERDUE_S:
            self._load("D1 allow-list overdue")
        w = inp.get("worker") or {}
        if w.get("production_present") is True:
            self._safety("production Worker present")
        if live_from_gate:
            ts = w.get("ts")
            if w.get("read_ok") is True and isinstance(ts, (int, float)) and now - ts <= WORKER_STALE_S:
                self.last_worker = max(self.last_worker or ts, ts)
                if w.get("production_present") is not False:
                    self._safety("production Worker presence not verified absent")
                if w.get("dry_run") is None:
                    self._safety("QA Worker DRY_RUN missing")
                elif w.get("dry_run") != "false":
                    self._safety(f"QA Worker DRY_RUN changed unexpectedly to {w.get('dry_run')!r}")
                exp = w.get("expected_version")
                if not exp or w.get("versions") != [[exp, 100]]:
                    self._safety(f"QA Worker not on the single 100% gate-passed version ({w.get('versions')} vs {exp})")
            if now - (self.last_worker if self.last_worker is not None else self.t0) > WORKER_STALE_S:
                self._safety(f"QA Worker state not verified for > {WORKER_STALE_S}s (Worker input missing, failed or stale)")
        if inp.get("manual_stop"):
            self._safety("manual-stop")
        if inp.get("load_safety_file"):
            self._safety("load.py SAFETY (safety-stop file)")
        # drain
        if drv.get("done") and self.restore_reason is None:
            units = drv.get("completed_units")
            if self.allocated is not None and units is not None and self.allocated == units:
                self.drained, self.verdict = True, "DRAINED"
                self.restore_reason = "PLANNED: drained"
            elif drv.get("last_sent_ts") is not None and now - drv["last_sent_ts"] > DRAIN_DEADLINE_S:
                self.verdict, self.restore_reason = "DRAIN_DEADLINE", "DEADLINE: 30 min after the last order"
            elif self.last_progress_t is not None and now - self.last_progress_t > NO_PROGRESS_S:
                self.verdict, self.restore_reason = "NO_PROGRESS", "NO PROGRESS: 10 min without a new allocation"
        if w.get("restored_verified"):
            self.restore_verified = True
        actions = ["RESTORE_DRY_RUN"] if self.restore_reason and not self.restore_verified else []
        due = {"d1_count": self.last_d1 is None or now - self.last_d1 >= D1_COUNT_EVERY_S,
               "allowlist": self.last_allow is None or now - self.last_allow >= ALLOWLIST_EVERY_S}
        return {"ts": now, "safety": list(self.safety), "load": list(self.load), "actions": actions, "due": due,
                "restore_reason": self.restore_reason, "restore_verified": self.restore_verified, "drained": self.drained,
                "verdict": self.verdict, "failwatch": self.fw.level,
                "throttled": [t for t in self.throttled if t >= now - 60],
                "wall_ms_max": max([v for t, v in self.walls if t >= now - 10] or [0]),
                "done_ts": sorted(v for v in self.done_orders.values() if v >= now - 60), "allocated": self.allocated}


# ---- one-tick CLI (live wiring; Stage 4 only) ----------------------------------------------------------------------------------
def tick_cli(d):
    """One tick: load the Guard from DIR/guardl-state.pkl (local, 0600, never shared), apply DIR/guardl-input.json (written by
    `collect`), write guardl.json + failwatch.json, create load-stop on any LOAD reason. Exit 10 = restore requested."""
    import pickle
    sp = os.path.join(d, "guardl-state.pkl")
    g = pickle.load(open(sp, "rb")) if os.path.exists(sp) else Guard()
    inp = read_json(os.path.join(d, "guardl-input.json")) if os.path.exists(os.path.join(d, "guardl-input.json")) else {}
    inp["manual_stop"] = os.path.exists(os.path.join(d, "manual-stop"))
    inp["load_safety_file"] = os.path.exists(os.path.join(d, "safety-stop"))
    out = g.tick(time.time(), inp)
    write_json(os.path.join(d, "guardl.json"), out)
    write_json(os.path.join(d, "failwatch.json"), g.fw.state())
    fd = os.open(sp + ".tmp", os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        pickle.dump(g, f)
    os.replace(sp + ".tmp", sp)
    if out["load"]:
        open(os.path.join(d, "load-stop"), "a").close()
    print(canonical({k: out[k] for k in ("safety", "load", "actions", "verdict")}))
    return 10 if out["actions"] else 0


def collect(d, now=None, d1_query=None):
    """Build DIR/guardl-input.json from the live sources. Read-only everywhere: tails (local files), sampler.jsonl (local), QA D1
    via tools/gs read-only SQL, and the livewin.py poller files: worker-poll.jsonl (QA Worker settings/deployments and the
    production Worker, read-only GETs) and wl-poll.jsonl (Workers Logs windows). Never a Shopify read. A missing poller file
    is passed on as missing input; the guard turns missing or stale input into a stop, never into a pass."""
    now = time.time() if now is None else now
    cfg = read_json(os.path.join(d, "guardl-config.json"))
    cid = str(cfg["competition_id"])
    offp = os.path.join(d, "guardl-offsets.json")
    offs = read_json(offp) if os.path.exists(offp) else {}
    events, thr = [], []
    for name in ("pre.jsonl", "pre2.jsonl"):
        path = os.path.join(d, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            f.seek(offs.get(name, 0))
            text = f.read()
            offs[name] = f.tell()
        for o in tail_objects(text):
            ev, t = tail_events(o)
            thr += t
            if ev:
                events.append(ev)
    write_json(offp, offs)
    inp = {"events": events, "throttled": thr}
    try:
        lines = open(os.path.join(d, "sampler.jsonl")).read().splitlines()
        subs = [json.loads(x) for x in lines[-30:] if '"subs"' in x]
        if subs:
            inp["sampler"] = {"ts": json.loads(lines[-1])["ts"], "subs_ts": subs[-1]["ts"], "subs_ok": subs[-1].get("subs_ok")}
    except (OSError, ValueError, IndexError):
        pass
    prev = read_json(os.path.join(d, "guardl.json")) if os.path.exists(os.path.join(d, "guardl.json")) else {}
    due = prev.get("due") or {"d1_count": True, "allowlist": True}
    d1_query = d1_query or _gs_d1
    if due.get("d1_count"):
        r = d1_query(f"SELECT COUNT(*) AS n FROM entry_number WHERE competition_id = '{cid}' AND status = 'ALLOCATED'")
        if r is not None:
            inp["d1_count"] = {"ts": now, "allocated": r[0]["n"]}
    ev = _driver_records(d)
    succ = [r for r in ev if r.get("kind") == "request" and r.get("outcome") == "SUCCESS"]
    if due.get("allowlist"):
        snap_sql = open(os.path.join(cfg["repo"], "qa", "b3-stress-harness", "b3stress", "snap.sql")).read()
        snap = d1_snapshot(d1_query, snap_sql)
        if snap is not None:
            ref = read_json(os.path.join(d, "d1-ref.json"))
            inp["allowlist"] = {"ts": now, "deviations": allowlist(ref, snap, cid, [x["qty"] for x in succ], cfg["pre_max_order_number"])}
    started = os.path.exists(os.path.join(d, f"load-{cid}.done"))
    sent = [r for r in ev if r.get("kind") == "request"]
    inp["driver"] = {"started": (min(r["t_send"] for r in sent) if sent else (now if started else None)),
                     "done": os.path.exists(os.path.join(d, "load-summary.json")),
                     "last_sent_ts": max((r["t_send"] for r in sent), default=None),
                     "completed_units": sum(x["qty"] for x in succ)}
    w = last_jsonl(os.path.join(d, "worker-poll.jsonl"))
    if w is not None:
        inp["worker"] = w
    inp["wl"], offs["wl-poll.jsonl"] = new_jsonl(os.path.join(d, "wl-poll.jsonl"), offs.get("wl-poll.jsonl", 0))
    write_json(offp, offs)
    inp["wl_from_ms"] = cfg.get("wl_from_ms")
    write_json(os.path.join(d, "guardl-input.json"), inp)
    return inp


SNAP_JSON_FIELDS = ("competitions", "entries", "allocations", "events", "put_summary", "integrity", "webhook_deliveries", "sqlite_seq")


def d1_snapshot(d1_query, snap_sql):
    """One read-only snap.sql row in parsed form (the form allowlist() and d1-ref.json use), or None if the read failed."""
    r = d1_query(snap_sql)
    if not r or not isinstance(r[0], dict) or r[0].get("marker") != "SW4C_SNAP":
        return None
    snap = dict(r[0])
    for k in SNAP_JSON_FIELDS:
        if isinstance(snap.get(k), str):
            snap[k] = json.loads(snap[k])
    return snap


def last_jsonl(path):
    """The newest complete JSON line of a poller file, or None (missing file, no complete line, unparseable)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().split(b"\n")[:-1]          # the last element is an incomplete line (or empty)
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None


def new_jsonl(path, offset):
    """Complete JSON lines appended since offset -> (objects, new offset). A partial last line is left for the next tick; a line
    that does not parse becomes an INCOMPLETE window, so a corrupt poller record can never pass silently."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    end = data.rfind(b"\n") + 1
    out = []
    for line in data[:end].split(b"\n"):
        if not line.strip():
            continue
        try:
            o = json.loads(line)
            out.append(o if isinstance(o, dict) else {"complete": False, "events": [], "problems": ["non-object line"]})
        except ValueError:
            out.append({"complete": False, "events": [], "problems": ["unparseable wl-poll line"]})
    return out, offset + end


def _driver_records(d):
    try:
        return [json.loads(x) for x in open(os.path.join(d, "load-evidence.jsonl")).read().splitlines() if x.strip()]
    except OSError:
        return []


def _gs_d1(sql):
    import subprocess
    repo = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
    r = subprocess.run([sys.executable, os.path.join(repo, "tools", "gs", "gs.py"), "d1", "query", "--json", sql],
                       capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        return None                      # a failed or refused read is never treated as an empty result
    try:
        rows = json.loads(r.stdout)
    except ValueError:
        return None
    return rows if isinstance(rows, list) else None


def main(argv=None):
    p = argparse.ArgumentParser(prog="guardl.py")
    p.add_argument("cmd", choices=["tick", "collect"]); p.add_argument("--state-dir", required=True)
    a = p.parse_args(argv)
    if a.cmd == "collect":
        collect(a.state_dir)
        return 0
    return tick_cli(a.state_dir)


if __name__ == "__main__":
    sys.exit(main())
