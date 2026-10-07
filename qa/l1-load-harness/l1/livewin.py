#!/usr/bin/env python3
"""H11 livewin.py: L1 Stage 4 live-window wiring (Stage 4 only, separately approved; offline-tested). READ-ONLY everywhere: Shopify
reads only, QA D1 SELECTs only (tools/gs), Cloudflare GETs and the Workers Logs query only. It never deploys, writes D1, changes
a Worker or sends a Shopify mutation. window.sh sequences it; the QA Worker is changed only by the existing B3 gate.sh/restore.sh.

The L1 state directory is the installed B3 directory ($S/b3stress), so the existing tails (pre.jsonl, pre2.jsonl, conf.jsonl),
heartbeat, gate.sh outputs (newver.txt, gate.txt, t0.txt) and restore.sh outputs land in it unchanged.

  install-check  --scratchpad S                      B3 harness installed in S/b3stress; allocator worktree clean at e917bb5
  worker-read    --state-dir D [--expect FILE]       one QA Worker reading (JSON) using fire.py's read-only GETs
  worker-before  --state-dir D                       pre-gate: production absent, DRY_RUN "true", one version at 100% ->
                                                     worker-before.json + dry-version.txt
  poll-worker    --state-dir D [--until-file F]      worker-poll.jsonl: one reading every WORKER_POLL_S
  poll-wl        --state-dir D --from-ms MS [--until-file F]
                                                     wl-poll.jsonl: contiguous Workers Logs windows (b3obs/obsq.py) from MS,
                                                     ending WL_LAG_S in the past, one every WL_POLL_S; only webhook invocations
  prep           --state-dir D --plan P --plan-sha S expected-subs.json, pre-max-order.json, d1-ref.json, drafts-open.json
  drafts-open    --state-dir D --plan P --plan-sha S drafts-open.json only (load.py requires it to be at most 30 min old)
  d1-same        --state-dir D [--out FILE]          exit 0 iff a fresh QA D1 snapshot equals d1-ref.json
  config         --state-dir D --plan P --plan-sha S --wl-from-ms MS --restore-cmd C --verify-cmd V   guardl-config.json
  slot           [--wait]                            exit 0 inside hh:01-04 / hh:31-34 (with --wait: wait for the next one)
  prelive-sweeps --state-dir D --since-ms MS --dry-version V   B3 prestrict.py + evidence.py continuity, unchanged rules
                                                     (exit 3 = the required sweeps are not captured yet: wait for a later slot)
  no-webhooks    --state-dir D --since-ms MS         exit 0 iff no tail saw a /webhooks/ request since MS (B3 hooks.py)
  check-state    --state-dir D --plan P --plan-sha S --phase prelive|guarded|armed|final
                                                     every required state file -> window-state-PHASE.json
  verify-restore --state-dir D                       prints exactly "true" iff restored: restoregate.txt PASSED, DRY_RUN "true"
                                                     on the single 100% restorever.txt version, production Worker absent
  final          --state-dir D                       failwatch over every tail event and every Workers Logs window ->
                                                     failwatch-final.json, with the Workers Logs coverage of the whole window
Exit codes: 0 ok, 2 refused / check failed, 3 not yet (slot)."""
import argparse, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
from common import (CAPACITY, N_ORDERS, ORDER_TAG, PREFIX, START_NUMBER, Refused, canonical, plan_hash, read_json,  # noqa: E402
                    scrub, sha256_file, validate_plan, write_json)
import guardl  # noqa: E402
from guardl import WL_LAG_S, WL_POLL_S, WORKER_POLL_S, WORKER_STALE_S, allowlist, d1_snapshot, tail_events, tail_objects  # noqa: E402
from failwatch import FailWatch  # noqa: E402

B3 = os.path.join(REPO, "qa", "b3-stress-harness")
POLICY = os.path.join(REPO, "tools", "gs", "policy.json")
SNAP_SQL = os.path.join(B3, "b3stress", "snap.sql")
DRAFTS_DOC = "query L1DraftsOpen($ids: [ID!]!) { nodes(ids: $ids) { __typename ... on DraftOrder { id name status tags } } }"
PRE_MAX_DOC = "query L1PreMaxOrder { orders(first: 5, sortKey: CREATED_AT, reverse: true) { nodes { id name } } }"
DRAFTS_BATCH = 50
PRECHECK_MAX_AGE_S = 1800              # load.py: drafts-open precheck no older than 30 min
ARMING_MAX_AGE_S = 1200                # fire.py check_live_gates: arming 0..1200 s old
GUARD_FRESH_S = 10.0                   # load.py FileSignals: guardl.json older than 10 s is a SAFETY reason
TAIL_FRESH_S = 30                      # a tail file not written for 30 s while the 4 s heartbeat runs is not live
EVIDENCE_PENDING_BUDGET_S = 170        # goliveb.sh: PENDING (tail gap awaiting Workers Logs) waited for at most ~170 s
ALLOCATOR_COMMIT = "e917bb504a07bf19543555cd037281e1b9e47683"   # gate.sh / restore.sh refuse any other allocator checkout
VERSION_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _p(d, name):
    return os.path.join(d, name)


def _txt(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _fire():
    from shop import load_fire
    return load_fire()


def _append(path, obj, secrets=()):
    with open(path, "a") as f:
        f.write(scrub(canonical(obj), secrets) + "\n")


# ---- QA Worker reading (the same read-only GETs and rules as fire.py check_live_gates) -----------------------------------------
def worker_reading(cf, env, now, expected_version, fire=None):
    """{ts, read_ok, production_present, dry_run, versions, expected_version, http, problems}. read_ok is True only when all three
    GETs answered usably. production_present: False (404, or 200 with success false: the check_live_gates rule), True (200 and
    not success false), None (unverifiable). dry_run: the single DRY_RUN binding text, None if there is none, "<multiple>" if
    there are several. versions: [[version_id, percentage]] of the current deployment."""
    fire = fire or _fire()
    r = {"ts": now, "read_ok": False, "production_present": None, "dry_run": None, "versions": None,
         "expected_version": expected_version, "http": {}, "problems": []}
    acct, tok = env.get("CLOUDFLARE_ACCOUNT_ID"), env.get("CLOUDFLARE_API_TOKEN")
    if not acct or not tok:
        r["problems"].append("CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN not set")
        return r
    base = f"/client/v4/accounts/{acct}/workers/scripts"
    try:
        st, d = fire.cf_json(cf, f"{base}/{fire.PROD_WORKER}/settings", tok)
        r["http"]["production"] = st
        if st == 404 or (st == 200 and d.get("success") is False):
            r["production_present"] = False
        elif st == 200:
            r["production_present"] = True
        else:
            r["problems"].append(f"production Worker check unusable (HTTP {st})")
        st, s = fire.cf_json(cf, f"{base}/{fire.QA_WORKER}/settings", tok)
        r["http"]["settings"] = st
        settings_ok = st == 200 and s.get("success") is not False and isinstance((s.get("result") or {}).get("bindings"), list)
        if settings_ok:
            dry = [b.get("text") for b in s["result"]["bindings"] if b.get("name") == "DRY_RUN"]
            r["dry_run"] = dry[0] if len(dry) == 1 else (None if not dry else "<multiple>")
        else:
            r["problems"].append(f"QA Worker settings unusable (HTTP {st})")
        st, dp = fire.cf_json(cf, f"{base}/{fire.QA_WORKER}/deployments", tok)
        r["http"]["deployments"] = st
        deps = (dp.get("result") or {}).get("deployments") if st == 200 and dp.get("success") is not False else None
        if isinstance(deps, list) and deps:
            r["versions"] = [[v.get("version_id"), v.get("percentage")] for v in deps[0].get("versions") or []]
        else:
            r["problems"].append(f"QA Worker deployments unusable (HTTP {st})")
        r["read_ok"] = r["production_present"] is not None and settings_ok and r["versions"] is not None
    except Exception as e:                     # a transport failure is an unusable reading, never a good one
        r["problems"].append(f"transport error: {type(e).__name__}")
        r["read_ok"] = False
    return r


def poll(fn, out_path, every, clock, until, secrets=()):
    """Append fn(now) (when not None) to out_path every `every` seconds until until() is true."""
    n = 0
    while not until():
        t = clock.now()
        rec = fn(t)
        if rec is not None:
            _append(out_path, rec, secrets)
            n += 1
        clock.sleep(max(0.0, every - (clock.now() - t)))
    return n


# ---- Workers Logs windows ------------------------------------------------------------------------------------------------------
def _obsq():
    sys.path.insert(0, os.path.join(B3, "b3obs"))
    import obsq
    return obsq


class WLPoller:
    """Contiguous windows [next, now - WL_LAG_S]. A complete window advances `next`; an incomplete or failed one does not (the
    next poll re-queries from the same point) and is still written, so the guard's failwatch turns it into a LOAD STOP."""

    def __init__(self, from_ms, fetch, out_path=None):
        self.next, self.fetch = int(from_ms), fetch
        if out_path and os.path.exists(out_path):      # a restarted poller resumes after the last complete window
            for line in open(out_path).read().splitlines():
                try:
                    w = json.loads(line)
                except ValueError:
                    continue
                if w.get("complete") and isinstance(w.get("to_ms"), int) and w.get("from_ms", 0) <= self.next:
                    self.next = max(self.next, w["to_ms"] + 1)

    def once(self, now):
        hi = int((now - WL_LAG_S) * 1000)
        if hi < self.next:
            return None
        try:
            r = self.fetch(self.next, hi)
        except Exception as e:
            r = {"complete": False, "problems": [f"query error: {type(e).__name__}"], "invocations": []}
        w = guardl.wl_from_obsq(r)
        w.update(ts=now, from_ms=self.next, to_ms=hi, problems=list(r.get("problems") or []))
        if w["complete"]:
            self.next = hi + 1
        return w


# ---- prep: read-only snapshots taken before the gate ---------------------------------------------------------------------------
def _ok_body(st, body, what):
    if st != 200 or (body or {}).get("errors") or not (body or {}).get("data"):
        raise Refused(f"{what}: HTTP {st}, errors {str((body or {}).get('errors'))[:200]}")
    return body["data"]


def expected_subs(client, policy):
    from sampler import SUBS_DOC, subs_of
    st, _, body = client.post(SUBS_DOC)
    _ok_body(st, body, "webhook subscriptions")
    subs = subs_of(body)
    topics = sorted(t for _, t, _ in subs)
    pre = policy["webhooks"]["callback_prefix"]
    if topics != sorted(policy["webhooks"]["topics"]):
        raise Refused(f"webhook topics {topics} != expected {sorted(policy['webhooks']['topics'])}")
    bad = [u for _, _, u in subs if not str(u or "").startswith(pre)]
    if bad:
        raise Refused(f"{len(bad)} subscription callback(s) not on the QA Worker")
    return [list(x) for x in subs]


def pre_max_order(client):
    st, _, body = client.post(PRE_MAX_DOC)
    nodes = (_ok_body(st, body, "latest orders").get("orders") or {}).get("nodes") or []
    nums = [int(n["name"].lstrip("#")) for n in nodes if re.fullmatch(r"#\d+", str(n.get("name") or ""))]
    if not nums:
        raise Refused("no order number found")
    return max(nums)


def drafts_open(client, plan, plan_sha, now, sleep=time.sleep):
    """Every plan draft read back by exact id: OPEN, tagged qa-load + QAL + its own row tag. open = the number that pass."""
    from shop import is_throttled
    rows = plan["rows"]
    want = {r["draft_id"]: r for r in rows}
    ids = [r["draft_id"] for r in rows]
    ok, probs, seen = 0, [], set()
    for i in range(0, len(ids), DRAFTS_BATCH):
        chunk = ids[i:i + DRAFTS_BATCH]
        st, _, body = client.post(DRAFTS_DOC, {"ids": chunk})
        if is_throttled(body):
            raise Refused("drafts-open read throttled; nothing judged (re-run later)")
        nodes = _ok_body(st, body, "draft read").get("nodes")
        if not isinstance(nodes, list) or len(nodes) != len(chunk):
            raise Refused("draft read returned a different number of nodes")
        for gid, n in zip(chunk, nodes):
            r = want[gid]
            why = None
            if not n or n.get("__typename") != "DraftOrder" or n.get("id") != gid:
                why = "missing or not this draft"
            elif n.get("status") != "OPEN":
                why = f"status {n.get('status')}"
            elif not {ORDER_TAG, PREFIX, r["tag"]} <= set(n.get("tags") or []):
                why = f"tags {n.get('tags')}"
            if why:
                probs.append(f"{r['tag']} {gid}: {why}")
            else:
                ok += 1
            seen.add(gid)
        sleep(0.5)
    return {"ts": now, "plan_sha256": plan_sha, "open": ok, "expected": len(rows), "checked": len(seen), "problems": probs[:25],
            "problem_count": len(probs)}


def registration_problems(snap, cid):
    """The QA D1 reference must hold exactly the fresh L1 registration and pass the guard's own allow-list against itself."""
    p = []
    comp = [c for c in snap.get("competitions") or [] if str(c.get("competition_id")) == cid]
    if len(comp) != 1:
        return [f"{len(comp)} competition rows for {cid}"]
    c = comp[0]
    if (c.get("status"), c.get("prefix"), c.get("capacity"), c.get("start_number")) != ("OPEN", PREFIX, CAPACITY, START_NUMBER):
        p.append(f"competition {cid} is {c.get('status')}/{c.get('prefix')}/{c.get('capacity')}/{c.get('start_number')}")
    ents = [e for e in snap.get("entries") or [] if str(e[0]) == cid]
    exp = [[cid, START_NUMBER + k, f"{PREFIX}{START_NUMBER + k}", "AVAILABLE", None, 0] for k in range(CAPACITY)]
    if [[str(e[0]), e[1], e[2], e[3], e[4], e[8]] for e in ents] != exp:
        p.append(f"L1 pool is not exactly {PREFIX}{START_NUMBER}..{PREFIX}{START_NUMBER + CAPACITY - 1} AVAILABLE, never issued")
    if [a for a in snap.get("allocations") or [] if str(a[2]) == cid]:
        p.append("L1 competition already has allocations")
    return p


def d1_reference(d1_query, cid, pre_max):
    snap = d1_snapshot(d1_query, open(SNAP_SQL).read())
    if snap is None:
        raise Refused("QA D1 snapshot read failed")
    p = registration_problems(snap, cid) + allowlist(snap, snap, cid, [], pre_max)
    if p:
        raise Refused("QA D1 reference not the expected registered state: " + "; ".join(p[:5]))
    return snap


def load_plan(path, plan_sha):
    plan = read_json(path)
    probs = validate_plan(plan, bound=True)
    if probs:
        raise Refused("plan invalid: " + "; ".join(probs[:5]))
    if plan_hash(plan) != plan_sha:
        raise Refused("plan sha256 differs from the approved one")
    return plan


def guardl_config(plan, plan_sha, pre_max, wl_from_ms, restore_cmd, verify_cmd, repo=REPO):
    if not isinstance(pre_max, int) or not isinstance(wl_from_ms, int) or not restore_cmd or not verify_cmd:
        raise Refused("guardl config incomplete")
    return {"competition_id": str(plan["competition_id"]), "pre_max_order_number": pre_max, "repo": repo,
            "restore_cmd": restore_cmd, "verify_cmd": verify_cmd, "wl_from_ms": wl_from_ms, "plan_sha256": plan_sha}


# ---- state check -----------------------------------------------------------------------------------------------------------------
def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (TypeError, ValueError, OSError):
        return False


def check_state(d, plan, plan_sha, phase, now):
    """Problems with the state files the live run needs at `phase` (prelive: before the gate; armed: just before load.py;
    final: after the restore). Returns (problems, file inventory)."""
    p, cid = [], str(plan["competition_id"])

    def j(name):
        try:
            return read_json(_p(d, name))
        except (OSError, ValueError):
            p.append(f"{name} missing or unreadable")
            return None
    subs = j("expected-subs.json")
    if subs is not None and (not isinstance(subs, list) or len(subs) != 5):
        p.append("expected-subs.json is not the 5 allocator subscriptions")
    pm = j("pre-max-order.json")
    if pm is not None and not isinstance(pm.get("pre_max_order_number"), int):
        p.append("pre-max-order.json has no order number")
    ref = j("d1-ref.json")
    if ref is not None:
        if ref.get("marker") != "SW4C_SNAP":
            p.append("d1-ref.json is not a snap.sql snapshot")
        else:
            p += ["d1-ref.json: " + x for x in registration_problems(ref, cid)]
    dr = j("drafts-open.json")
    if dr is not None and phase != "final":
        if dr.get("plan_sha256") != plan_sha or dr.get("open") != len(plan["rows"]) or dr.get("open") != N_ORDERS:
            p.append(f"drafts-open.json: {dr.get('open')} of {len(plan['rows'])} OPEN, or for another plan")
        if not 0 <= now - float(dr.get("ts", 0)) <= PRECHECK_MAX_AGE_S:
            p.append("drafts-open.json older than 30 min")
    if phase != "final":
        for t in ("pre.jsonl", "pre2.jsonl"):
            try:
                if now - os.path.getmtime(_p(d, t)) > TAIL_FRESH_S:
                    p.append(f"{t} not written for > {TAIL_FRESH_S}s (tail not live)")
            except OSError:
                p.append(f"{t} missing")
    if phase in ("guarded", "armed", "final"):
        nv = _txt(_p(d, "newver.txt"))
        if not nv or not VERSION_RE.match(nv):
            p.append("newver.txt missing or not a version id")
        if _txt(_p(d, "gate.txt")) != "PASSED":
            p.append("gate.txt is not PASSED")
        cfg = j("guardl-config.json")
        if cfg is not None:
            if str(cfg.get("competition_id")) != cid or cfg.get("plan_sha256") != plan_sha:
                p.append("guardl-config.json is for another competition or plan")
            if pm is not None and cfg.get("pre_max_order_number") != pm.get("pre_max_order_number"):
                p.append("guardl-config.json pre_max_order_number differs from pre-max-order.json")
            if not isinstance(cfg.get("wl_from_ms"), int) or not cfg.get("restore_cmd") or not cfg.get("verify_cmd"):
                p.append("guardl-config.json incomplete (wl_from_ms / restore_cmd / verify_cmd)")
    if phase == "armed":
        try:
            age = now - int(_txt(_p(d, "qag-start"))) / 1000
            if not 0 <= age <= ARMING_MAX_AGE_S:
                p.append(f"qag-start is {age:.0f}s old")
        except (TypeError, ValueError):
            p.append("qag-start missing")
    if phase in ("guarded", "armed"):
        if not _alive(_txt(_p(d, "guard.pid"))):
            p.append("guardl.sh is not running")
        g = j("guardl.json")
        if g is not None:
            if now - float(g.get("ts", 0)) > GUARD_FRESH_S:
                p.append("guardl.json stale")
            if g.get("safety") or g.get("load") or g.get("actions"):
                p.append(f"guard not clear: safety {g.get('safety')} load {g.get('load')}")
        w = guardl.last_jsonl(_p(d, "worker-poll.jsonl"))
        if not w or not w.get("read_ok") or now - float(w.get("ts", 0)) > WORKER_STALE_S:
            p.append("no fresh good QA Worker reading")
        elif (w.get("production_present"), w.get("dry_run"), w.get("versions")) != (False, "false", [[_txt(_p(d, "newver.txt")), 100]]):
            p.append(f"QA Worker not live on the gate-passed version: {w.get('dry_run')} {w.get('versions')}")
        for f in ("sampler.jsonl", "wl-poll.jsonl"):
            if not os.path.exists(_p(d, f)):
                p.append(f"{f} missing")
        if os.path.exists(_p(d, "manual-stop")):
            p.append("manual-stop present")
        if os.path.exists(_p(d, f"load-{cid}.done")):
            p.append("one-shot marker already exists")
    files = {}
    for f in ("expected-subs.json", "pre-max-order.json", "d1-ref.json", "drafts-open.json", "guardl-config.json", "pre.jsonl",
              "pre2.jsonl", "newver.txt", "gate.txt", "qag-start", "guard.pid"):
        if os.path.exists(_p(d, f)):
            files[f] = {"sha256": sha256_file(_p(d, f)), "bytes": os.path.getsize(_p(d, f))}
    return p, files


# ---- restore verification ------------------------------------------------------------------------------------------------------
def restore_verified(cf, env, d, now, fire=None):
    rv = _txt(_p(d, "restorever.txt"))
    if _txt(_p(d, "restoregate.txt")) != "PASSED" or not rv:
        return False
    w = worker_reading(cf, env, now, rv, fire)
    return bool(w["read_ok"] and w["production_present"] is False and w["dry_run"] == "true" and w["versions"] == [[rv, 100]])


# ---- final failwatch over the whole window -------------------------------------------------------------------------------------
def final(d):
    fw = FailWatch()
    for name in ("pre.jsonl", "pre2.jsonl"):
        try:
            text = open(_p(d, name), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for o in tail_objects(text):
            ev, _ = tail_events(o)
            if ev:
                fw.observe(ev, "tail")
    cfg = read_json(_p(d, "guardl-config.json"))
    wins, _ = guardl.new_jsonl(_p(d, "wl-poll.jsonl"), 0)
    hi, gaps, incomplete = cfg["wl_from_ms"] - 1, [], 0
    for w in sorted(wins, key=lambda x: (x.get("from_ms") or 0)):
        fw.confirm_wl(w.get("events") or [], bool(w.get("complete")))
        if not w.get("complete"):
            incomplete += 1
            continue
        if w["from_ms"] > hi + 1:
            gaps.append([hi + 1, w["from_ms"] - 1])
        hi = max(hi, w["to_ms"])
    out = {"failwatch": fw.state(), "wl_coverage": {"from_ms": cfg["wl_from_ms"], "to_ms": hi, "gaps": gaps,
                                                    "incomplete_windows": incomplete, "windows": len(wins)}}
    write_json(_p(d, "failwatch-final.json"), out)
    return out


# ---- pre-live sweeps and continuity (B3 checkers, unchanged rules) ---------------------------------------------------------------
def prelive_sweeps(d, since_ms, dry_version, run=subprocess.run, clock=time):
    """B3 prestrict.py (latest trailing and deep dry-run sweeps all zero on dry_version), then B3 evidence.py tail continuity from
    60 s before the earlier of those sweeps to now - 15 s, waiting while it is PENDING (as goliveb.sh does). Returns (ok, report)."""
    r = run(["python3", _p(d, "prestrict.py"), d, str(since_ms), dry_version], capture_output=True, text=True)
    try:
        ps = json.loads(r.stdout)
    except ValueError:
        return False, {"prestrict": "unparseable", "rc": r.returncode}
    if r.returncode == 3:
        return "WAIT", {"prestrict": ps, "rc": 3}
    if r.returncode != 0:
        return False, {"prestrict": ps, "rc": r.returncode}
    ids = {s["run_id"] for s in ps["sweeps"]}
    sw = run(["python3", _p(d, "sweeps.py"), d, str(since_ms)], capture_output=True, text=True)
    ts = [json.loads(x)["ts"] for x in sw.stdout.splitlines() if x.strip() and any(
        isinstance(m, dict) and m.get("run_id") in ids for m in json.loads(x)["logs"])]
    if not ts:
        return False, {"prestrict": ps, "continuity": "required sweeps not found in the tails"}
    t_from, start = min(ts) - 60000, clock.time()
    while True:
        ev = run(["python3", _p(d, "evidence.py"), d, str(t_from), str(int(clock.time() * 1000) - 15000), "9999999999999", "true"],
                 capture_output=True, text=True)
        if ev.returncode != 4 or clock.time() - start > EVIDENCE_PENDING_BUDGET_S:
            break
        clock.sleep(3)
    try:
        evj = json.loads(ev.stdout)
    except ValueError:
        evj = {"result": "unparseable"}
    return ev.returncode == 0, {"prestrict": ps, "continuity_from_ms": t_from, "evidence": evj, "evidence_rc": ev.returncode}


def webhooks_since(d, since_ms, run=subprocess.run):
    text = ""
    for f in ("pre.jsonl", "pre2.jsonl", "conf.jsonl", "obs.jsonl"):
        try:
            text += open(_p(d, f), encoding="utf-8", errors="replace").read()
        except OSError:
            pass
    r = run(["python3", _p(d, "hooks.py"), "/dev/stdin", "all", str(since_ms)], input=text, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return len([x for x in r.stdout.splitlines() if x.strip()])


def install_problems(s, run=subprocess.run):
    d, a, p = os.path.join(s, "b3stress"), os.path.join(s, "b3qa", "greenside-entry-allocator"), []
    for f in ("gate.sh", "restore.sh", "strictgate.sh", "postcheck.sh", "supervisor.sh", "heartbeat.sh", "dsnap.sh", "prestrict.py",
              "sweeps.py", "evidence.py", "hooks.py"):
        t = _txt(os.path.join(d, f))
        if t is None:
            p.append(f"b3stress/{f} missing")
        elif "__SCRATCHPAD__" in t:
            p.append(f"b3stress/{f} not installed (placeholder left)")
    if os.path.exists(os.path.join(d, "stop")):
        p.append("b3stress/stop exists (a spent directory: install into a fresh scratchpad)")
    spent = [f for f in ("qag-start", "guardl-config.json") if os.path.exists(os.path.join(d, f))]
    spent += [f for f in (os.listdir(d) if os.path.isdir(d) else []) if f.startswith("load-") and f.endswith(".done")]
    if spent:
        p.append(f"b3stress holds L1 run state {sorted(spent)} (a used directory: install into a fresh scratchpad)")
    head = run(["git", "-C", a, "rev-parse", "HEAD"], capture_output=True, text=True)
    dirty = run(["git", "-C", a, "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True)
    if head.returncode != 0 or head.stdout.strip() != ALLOCATOR_COMMIT or dirty.returncode != 0 or dirty.stdout.strip():
        p.append("allocator worktree missing, not at e917bb5, or not clean")
    if not os.path.isdir(os.path.join(a, "node_modules")):
        p.append("allocator worktree has no node_modules (npm ci)")
    return p


def worker_before_problems(w):
    p = []
    if not w["read_ok"]:
        p.append("QA Worker reading failed: " + "; ".join(w["problems"]))
    if w["production_present"] is not False:
        p.append(f"production Worker present or unverifiable ({w['production_present']})")
    if w["dry_run"] != "true":
        p.append(f"QA Worker DRY_RUN is {w['dry_run']!r}, expected 'true' before the gate")
    if not (isinstance(w["versions"], list) and len(w["versions"]) == 1 and w["versions"][0][1] == 100 and w["versions"][0][0]):
        p.append(f"QA Worker is not on one version at 100% ({w['versions']})")
    return p


def in_slot(now):
    m = int(now // 60) % 30
    return 1 <= m <= 4


# ---- CLI -------------------------------------------------------------------------------------------------------------------------
class RealClock:
    now = staticmethod(time.time)
    sleep = staticmethod(time.sleep)


def _gs_d1(sql):
    return guardl._gs_d1(sql)


def main(argv=None):
    p = argparse.ArgumentParser(prog="livewin.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    ic = sub.add_parser("install-check"); ic.add_argument("--scratchpad", required=True)
    for name in ("worker-read", "worker-before", "poll-worker", "poll-wl", "prep", "drafts-open", "d1-same", "config", "prelive-sweeps", "no-webhooks",
                 "check-state", "verify-restore", "final"):
        s = sub.add_parser(name)
        s.add_argument("--state-dir", required=True)
        if name in ("prep", "drafts-open", "config", "check-state"):
            s.add_argument("--plan", required=True); s.add_argument("--plan-sha", required=True)
        if name in ("poll-worker", "poll-wl"):
            s.add_argument("--until-file")
        if name == "poll-wl":
            s.add_argument("--from-ms", type=int, required=True)
        if name == "config":
            s.add_argument("--wl-from-ms", type=int, required=True)
            s.add_argument("--restore-cmd", required=True); s.add_argument("--verify-cmd", required=True)
        if name in ("prelive-sweeps", "no-webhooks"):
            s.add_argument("--since-ms", type=int, required=True)
        if name == "prelive-sweeps":
            s.add_argument("--dry-version", required=True)
        if name == "check-state":
            s.add_argument("--phase", choices=("prelive", "guarded", "armed", "final"), required=True)
        if name == "d1-same":
            s.add_argument("--out")
        if name == "worker-read":
            s.add_argument("--expect")
    sl = sub.add_parser("slot"); sl.add_argument("--wait", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.cmd == "slot":
            while not in_slot(time.time()):
                if not a.wait:
                    print("outside the hh:01-04 / hh:31-34 slot")
                    return 3
                time.sleep(5)
            print("in slot")
            return 0
        if a.cmd == "install-check":
            probs = install_problems(a.scratchpad)
            print(canonical({"ok": not probs, "problems": probs}))
            return 0 if not probs else 2
        d = a.state_dir
        if a.cmd in ("worker-read", "worker-before", "poll-worker", "verify-restore"):
            fire = _fire()
            cf = fire.CloudflareTransport()
        if a.cmd == "worker-read":
            exp = _txt(a.expect) if a.expect else None
            r = worker_reading(cf, os.environ, time.time(), exp, fire)
            print(canonical(r))
            return 0 if r["read_ok"] else 2
        if a.cmd == "worker-before":
            r = worker_reading(cf, os.environ, time.time(), None, fire)
            probs = worker_before_problems(r)
            write_json(_p(d, "worker-before.json"), dict(r, problems_before=probs))
            if not probs:
                with open(_p(d, "dry-version.txt"), "w") as f:
                    f.write(r["versions"][0][0] + "\n")
            print(canonical({"ok": not probs, "problems": probs, "versions": r["versions"], "dry_run": r["dry_run"]}))
            return 0 if not probs else 2
        if a.cmd == "poll-worker":
            until = (lambda: os.path.exists(a.until_file)) if a.until_file else (lambda: False)
            poll(lambda now: worker_reading(cf, os.environ, now, _txt(_p(d, "newver.txt")), fire), _p(d, "worker-poll.jsonl"),
                 WORKER_POLL_S, RealClock(), until)
            return 0
        if a.cmd == "poll-wl":
            until = (lambda: os.path.exists(a.until_file)) if a.until_file else (lambda: False)
            out = _p(d, "wl-poll.jsonl")
            poll(WLPoller(a.from_ms, _obsq().fetch_window, out).once, out, WL_POLL_S, RealClock(), until)
            return 0
        if a.cmd == "verify-restore":
            print("true" if restore_verified(cf, os.environ, d, time.time(), fire) else "false")
            return 0
        if a.cmd == "final":
            out = final(d)
            print(canonical({"failwatch": out["failwatch"]["level"], "wl_coverage": out["wl_coverage"]}))
            return 0
        if a.cmd == "no-webhooks":
            n = webhooks_since(d, a.since_ms)
            print(f"webhooks since {a.since_ms}: {n}")
            return 0 if n == 0 else 2
        if a.cmd == "prelive-sweeps":
            ok, rep = prelive_sweeps(d, a.since_ms, a.dry_version)
            write_json(_p(d, "prelive-sweeps.json"), rep)
            print(canonical({"ok": ok, "evidence_rc": rep.get("evidence_rc")}))
            return 3 if ok == "WAIT" else (0 if ok is True else 2)
        if a.cmd == "d1-same":
            snap = d1_snapshot(_gs_d1, open(SNAP_SQL).read())
            if snap is None:
                print("QA D1 snapshot read failed")
                return 2
            if a.out:
                write_json(a.out, snap)
            same = snap == read_json(_p(d, "d1-ref.json"))
            print(f"d1 identical to reference: {same}")
            return 0 if same else 2
        plan = load_plan(a.plan, a.plan_sha)
        if a.cmd == "check-state":
            probs, files = check_state(d, plan, a.plan_sha, a.phase, time.time())
            write_json(_p(d, f"window-state-{a.phase}.json"), {"phase": a.phase, "ok": not probs, "problems": probs, "files": files,
                                                               "ts": time.time()})
            print(canonical({"phase": a.phase, "ok": not probs, "problems": probs}))
            return 0 if not probs else 2
        if a.cmd == "config":
            pm = read_json(_p(d, "pre-max-order.json"))["pre_max_order_number"]
            write_json(_p(d, "guardl-config.json"), guardl_config(plan, a.plan_sha, pm, a.wl_from_ms, a.restore_cmd, a.verify_cmd))
            print("guardl-config.json written")
            return 0
        from shop import Client
        from gs import read_only_graphql
        if not read_only_graphql(DRAFTS_DOC)[0]:
            raise Refused("drafts document is not read-only")
        sd = Client("stress_driver", {DRAFTS_DOC})
        if not sd.scopes() <= {"write_draft_orders", "read_draft_orders"}:
            raise Refused(f"Stress Driver scopes {sorted(sd.scopes())} are not write_draft_orders (+implied read)")
        if a.cmd == "prep":
            from sampler import SUBS_DOC
            al = Client("allocator", {SUBS_DOC, PRE_MAX_DOC})
            write_json(_p(d, "expected-subs.json"), expected_subs(al, read_json(POLICY)))
            pm = pre_max_order(al)
            write_json(_p(d, "pre-max-order.json"), {"pre_max_order_number": pm, "ts": time.time()})
            write_json(_p(d, "d1-ref.json"), d1_reference(_gs_d1, str(plan["competition_id"]), pm))
        dr = drafts_open(sd, plan, a.plan_sha, time.time())
        write_json(_p(d, "drafts-open.json"), dr)
        print(canonical({k: dr[k] for k in ("open", "expected", "problem_count")}))
        return 0 if dr["open"] == dr["expected"] == N_ORDERS else 2
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
