#!/usr/bin/env python3
"""Deterministic offline self-test for b3stress/fire.py (the S4 fire script). No network, no credentials, no Shopify/Cloudflare access.
A fake Shopify + Cloudflare (same transport interface as the real ones) records every request; the tests assert what was, and above all
what was NOT, sent. A simulated clock plus a connection-expiry model reproduce the real 30 Sep 2026 failure (connections idle behind the
proxy for more than ~10-20 s die with SSLEOFError): the OLD architecture (open, wait, send) is re-implemented here and must fail, the
repaired one must succeed. Usage: firetest.py B3STRESS_DIR  -> PASS/FAIL lines and 'FIRE SELF-TEST: n/m passed'; exit 0 iff all pass."""
import sys
sys.dont_write_bytecode = True
import copy, hashlib, importlib.util, io, json, os, ssl, subprocess, tempfile, threading, time

D = os.path.abspath(sys.argv[1])
HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.join(HERE, "..", "evidence", "s4-2026-09-30-failed")
spec = importlib.util.spec_from_file_location("fire", os.path.join(D, "fire.py"))
F = importlib.util.module_from_spec(spec); spec.loader.exec_module(F)
NM = [d["name"] for d in F.FIXTURE["drafts"]]                    # draft names of the pinned fixture
TOKEN, CID, CSEC, CFTOK = "TOK-SECRET-9f3a", "CID-SECRET-11aa", "CSEC-SECRET-22bb", "CFTOK-SECRET-33cc"
RES = []


def chk(name, ok, detail=""):
    RES.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)[:300]))


class FakeClock:
    """Real time plus an offset that sleep() advances instantly: a 10-minute wait costs no real time, and connection idle time is simulated."""
    def __init__(self): self.off, self.lock = 0.0, threading.Lock()
    def wall(self): return time.time() + self.off
    def wall_ns(self): return int(self.wall() * 1e9)
    def mono_ns(self): return time.perf_counter_ns() + int(self.off * 1e9)
    def sleep(self, s):
        if s > 0:
            with self.lock: self.off += s


class Resp:
    def __init__(self, status, body, headers=None): self.status, self._b, self._h = status, body, headers or {}
    def read(self): return self._b
    def getheader(self, k): return self._h.get(k.lower())


class World:
    def __init__(self):
        self.clock = FakeClock()
        self.drafts = {d["id"]: {"id": d["id"], "name": d["name"], "status": "OPEN", "tags": ["qa-stress", F.FIXTURE["prefix"]], "currencyCode": "GBP", "qty": d["qty"],
                                 "title": F.FIXTURE["line_title"], "amount": f"{d['qty']:.2f}"} for d in F.FIXTURE["drafts"]}
        self.scope, self.app, self.shop = "write_draft_orders", F.APP_NAME, F.SHOP
        self.scopes = ["write_draft_orders", "read_draft_orders"]
        self.schema_args = ["id", "paymentPending", "paymentGatewayId", "sourceName"]
        self.extra_list = []
        self.fail = {}                      # draft id -> 'http500' | 'usererror' | 'exception' | 'leak'   (mutation outcome)
        self.probe_fail = {}                # draft id -> 'http500' | 'exception' | 'noid' | 'notopen'     (pre-release probe outcome)
        self.probe_sim_delay = {}           # draft id -> simulated seconds the probe takes
        self.kill_after_probe = set()       # draft ids whose connection dies right after its probe (as an idle expiry would)
        self.broken = set()                 # connection indexes that raise SSLEOFError on any request while still reporting is_open()
        self.no_request_id = False          # responses to mutations carry no x-request-id
        self.same_request_id = False
        self.idle_limit = None              # simulated seconds of idle after which a connection is dead (None = never)
        self.log, self.opened, self.opens, self.mutations, self.in_flight, self.peak, self.lock = [], 0, [], [], 0, 0, threading.Lock()
        self.latency, self.closed_conn_index = 0.08, None


def doc_of(body):
    q = json.loads(body)["query"]
    for n in ("MUTATION", "PROBE", "CHECK_DRAFT", "CHECK_APP", "CHECK_SCHEMA", "CHECK_LIST"):
        if q == getattr(F, n): return n
    return "OTHER"


class FConn:
    def __init__(self, w, idx):
        self.w, self.idx, self.closed, self.dead = w, idx, False, False
        self.last = w.clock.wall()

    def is_open(self): return not self.closed and self.w.closed_conn_index != self.idx
    def close(self): self.closed = True

    def _alive(self):
        w = self.w
        if self.dead or self.idx in w.broken or (w.idle_limit is not None and w.clock.wall() - self.last > w.idle_limit):
            self.dead = True
            raise ssl.SSLEOFError(8, "EOF occurred in violation of protocol")

    def _answer(self, method, path, body, headers):
        w = self.w
        w.log.append({"method": method, "path": path, "headers": sorted(headers or {}), "body": body, "host": F.SHOP, "idx": self.idx})
        if path == F.TOKEN_PATH:
            return 200, {}, json.dumps({"access_token": TOKEN, "scope": w.scope}).encode()
        doc = doc_of(body); v = json.loads(body).get("variables") or {}
        assert headers.get("X-Shopify-Access-Token") == TOKEN
        rid = {"x-request-id": "rid-" + str(len(w.log))}
        if doc == "CHECK_APP":
            d = {"shop": {"myshopifyDomain": w.shop}, "currentAppInstallation": {"app": {"title": w.app}, "accessScopes": [{"handle": h} for h in w.scopes]}}
        elif doc == "CHECK_SCHEMA":
            d = {"m": {"fields": [{"name": "draftOrderComplete", "args": [{"name": a, "type": {"kind": "NON_NULL" if a == "id" else "SCALAR", "name": None if a == "id" else "ID", "ofType": {"name": "ID"} if a == "id" else None}} for a in w.schema_args]}]},
                 "p": {"fields": [{"name": "draftOrder"}, {"name": "userErrors"}]}}
        elif doc == "CHECK_DRAFT":
            x = w.drafts.get(v["id"])
            d = {"draftOrder": None if not x else {"id": x["id"], "name": x["name"], "status": x["status"], "tags": x["tags"], "currencyCode": x["currencyCode"],
                 "totalPriceSet": {"shopMoney": {"amount": x["amount"], "currencyCode": x["currencyCode"]}}, "lineItems": {"nodes": [{"quantity": x["qty"], "title": x["title"]}]}}}
        elif doc == "CHECK_LIST":
            d = {"draftOrders": {"nodes": [{"id": x["id"], "name": x["name"], "status": x["status"], "tags": x["tags"]} for x in list(w.drafts.values())[::-1]] + w.extra_list}}
        elif doc == "PROBE":
            x = w.drafts[v["id"]]; d = {"draftOrder": {"id": x["id"], "name": x["name"], "status": x["status"]}}
        else:
            raise AssertionError("unexpected document " + doc)
        return 200, rid, json.dumps({"data": d, "extensions": {"cost": {"throttleStatus": {"currentlyAvailable": 1990}}}}).encode()

    def request(self, method, path, body, headers):
        w = self.w; self._alive()
        if path == F.GRAPHQL_PATH and doc_of(body) == "PROBE":     # the pre-release probe (worker threads use request())
            did = json.loads(body)["variables"]["id"]
            mode = w.probe_fail.get(did)
            if w.probe_sim_delay.get(did): w.clock.sleep(w.probe_sim_delay[did])
            if mode == "exception": raise TimeoutError("probe timeout")
            st, h, raw = self._answer(method, path, body, headers)
            if mode == "http500": st, raw = 500, b'{"errors":[{"message":"boom"}]}'
            if mode == "noid": h = {}
            if mode == "notopen": raw = json.dumps({"data": {"draftOrder": {"id": did, "name": "x", "status": "COMPLETED"}}}).encode()
            self.last = w.clock.wall()
            if did in w.kill_after_probe: self.dead = True
            return st, h, raw
        r = self._answer(method, path, body, headers); self.last = w.clock.wall(); return r

    def send_phase(self, method, path, body, headers):
        w = self.w; self._alive()
        if path != F.GRAPHQL_PATH or doc_of(body) != "MUTATION":
            st, h, raw = self._answer(method, path, body, headers)
            if doc_of(body) == "PROBE":
                with w.lock: w.in_flight += 1; w.peak = max(w.peak, w.in_flight)

                def recv_probe():
                    time.sleep(w.latency)
                    with w.lock: w.in_flight -= 1
                    self.last = w.clock.wall()
                    return Resp(st, raw, h)
                return recv_probe
            return lambda: Resp(st, raw, h)
        w.log.append({"method": method, "path": path, "headers": sorted(headers), "body": body, "host": F.SHOP, "idx": self.idx})
        did = json.loads(body)["variables"]["id"]
        with w.lock: w.mutations.append(json.loads(body)); w.in_flight += 1; w.peak = max(w.peak, w.in_flight)

        def recv():
            time.sleep(w.latency)
            with w.lock: w.in_flight -= 1
            self.last = w.clock.wall()
            mode = w.fail.get(did)
            hid = {} if w.no_request_id else {"x-request-id": "rid-same" if w.same_request_id else "rid-" + did[-4:]}
            if mode == "exception": raise TimeoutError("simulated timeout")
            if mode == "leak": return Resp(200, json.dumps({"errors": [{"message": "invalid access token " + TOKEN}]}).encode(), hid)
            if mode == "http500": return Resp(500, b'{"errors":[{"message":"boom"}]}', hid)
            x = w.drafts[did]
            if mode == "usererror": return Resp(200, json.dumps({"data": {"draftOrderComplete": {"draftOrder": None, "userErrors": [{"field": ["id"], "message": "nope"}]}}}).encode(), hid)
            x["status"] = "COMPLETED"
            return Resp(200, json.dumps({"data": {"draftOrderComplete": {"draftOrder": {"id": x["id"], "name": x["name"], "status": "COMPLETED"}, "userErrors": []}},
                                         "extensions": {"cost": {"throttleStatus": {"currentlyAvailable": 1900}}}}).encode(), hid)
        return recv


class FTx:
    def __init__(self, w): self.w = w
    def open(self):
        self.w.opened += 1; self.w.opens.append(self.w.clock.wall()); return FConn(self.w, self.w.opened)


class CFConn:
    def __init__(self, cf): self.cf = cf
    def close(self): pass
    def request(self, method, path, body, headers):
        cf = self.cf; assert headers["Authorization"] == "Bearer " + CFTOK and method == "GET"
        cf.paths.append(path)
        j = lambda o, st=200: (st, {}, json.dumps(o).encode())
        if path.endswith(f"/{F.PROD_WORKER}/settings"):
            return j({"success": True, "result": {"bindings": []}}) if cf.prod_present else j({"success": False}, 404)
        if path.endswith(f"/{F.QA_WORKER}/deployments"):
            return j({"result": {"deployments": [{"versions": [{"version_id": cf.version, "percentage": cf.pct}]}]}})
        if path.endswith(f"/{F.QA_WORKER}/settings"):
            return j({"result": {"bindings": [{"name": "DRY_RUN", "text": cf.dry}]}})
        raise AssertionError("unexpected Cloudflare path " + path)


class FCF:
    def __init__(self): self.prod_present, self.version, self.pct, self.dry, self.paths = False, "v-live-1", 100, "false", []
    def open(self): return CFConn(self)


def state_dir(w, **over):
    d = tempfile.mkdtemp(prefix="firetest-")
    cfg = {k: F.FIXTURE[k] for k in ("competition_id", "product_gid", "prefix", "variant_gid", "capacity", "order_quantities")}
    cfg["draft_orders"] = copy.deepcopy(F.FIXTURE["drafts"]); cfg.update(over.get("cfg", {}))
    json.dump(cfg, open(f"{d}/stress.json", "w"))
    for name, val in {"newver.txt": "v-live-1", "gate.txt": "PASSED", "qag-start": str(int(w.clock.wall() * 1000) - 5000), "guard.pid": str(os.getpid())}.items():
        if over.get(name, "") is None: continue
        open(f"{d}/{name}", "w").write(over.get(name, val))
    return d


ENV = {"QA_STRESS_SHOPIFY_CLIENT_ID": CID, "QA_STRESS_SHOPIFY_CLIENT_SECRET": CSEC, "CLOUDFLARE_API_TOKEN": CFTOK, "CLOUDFLARE_ACCOUNT_ID": "acct"}
LIVE = ["--confirm", F.CONFIRM_PHRASE]
fa = lambda w, seconds: ["--fire-at", str(int(w.clock.wall() * 1000) + int(seconds * 1000))]


def run(mode, w=None, cf=None, extra=(), sd=None, env=ENV, **over):
    w = w or World(); cf = cf or FCF(); sd = sd or state_dir(w, **over); out = io.StringIO()
    rc = F.main([mode, "--state-dir", sd, "--out", sd + "/evidence.json", *extra], env=env, tx=FTx(w), cf=cf, out=out, clock=w.clock)
    return rc, out.getvalue(), w, cf, sd


def ev_of(sd): return json.load(open(sd + "/evidence.json"))


# ---- 1. constants, allow-list, transport invariants ------------------------------------------------------------------------------------
chk("target: shop host, API path and app are constants", F.SHOP == "7r5csb-1j.myshopify.com" and F.GRAPHQL_PATH == "/admin/api/2025-10/graphql.json" and F.APP_NAME == "Greenside QA Stress Driver")
chk("target: exactly four fixture drafts with quantities 1,1,2,2 summing to capacity 6", [d["qty"] for d in F.FIXTURE["drafts"]] == [1, 1, 2, 2] and sum(d["qty"] for d in F.FIXTURE["drafts"]) == F.FIXTURE["capacity"] == 6)
chk("target: no QAH draft or product is in the fixture", not any(x in json.dumps(F.FIXTURE) for x in ("1613611073910", "1613611106678", "1613611139446", "1613611172214", "15903096668534")))
chk("documents: exactly one mutation document, draftOrderComplete only, no paymentPending, no order/variant/product selection",
    F.MUTATION.count("mutation") == 1 and "draftOrderComplete(id: $id)" in F.MUTATION and "paymentPending" not in F.MUTATION
    and not any(f in F.MUTATION for f in ("order {", "variant", "product")) and all("mutation" not in q for q in F.ALLOWED_DOCUMENTS - {F.MUTATION}))
chk("documents: no allow-listed read document selects fields the Stress Driver cannot read", not any(f in q for q in F.ALLOWED_DOCUMENTS for f in ("variant {", "product {", " order {", "inventory")))
src = open(os.path.join(D, "fire.py")).read()
chk("source: the mutation keyword appears only in the MUTATION constant; no env/CLI host override; no retry loop",
    src.count("mutation Complete") == 1 and "--shop" not in src and "--host" not in src and 'environ["SHOP' not in src
    and not __import__("re").search(r"\bretr(y|ies)\b", src.lower().replace("no retries", "")))
chk("invariant: max idle at release is at most half the idle time observed to survive; max age is below the idle time observed dead",
    F.MAX_CONN_IDLE_S * 2 <= F.PROXY_IDLE_OBSERVED_SURVIVED_S and F.MAX_CONN_AGE_S < F.PROXY_IDLE_OBSERVED_DEAD_S and F.PROXY_IDLE_OBSERVED_SURVIVED_S < F.PROXY_IDLE_OBSERVED_DEAD_S, (F.MAX_CONN_IDLE_S, F.MAX_CONN_AGE_S))
chk("invariant: the pre-lead leaves room for opening and probing inside the maximum connection age", F.PRE_LEAD_S + 1.5 <= F.MAX_CONN_AGE_S and F.PRE_LEAD_S < F.MAX_CONN_IDLE_S, (F.PRE_LEAD_S, F.MAX_CONN_AGE_S))
QAI_IDS = ("15905304379766", "58850638201206", "1613906051446", "1613906116982", "1613906149750", "1613906182518")
chk("invariant: the failed QAI fixture is retired", "15905304379766" in F.RETIRED_COMPETITIONS)
chk("invariant: the pinned fixture is fresh: not retired and sharing no product, variant or draft id with QAI", F.FIXTURE["competition_id"] not in F.RETIRED_COMPETITIONS and not any(x in json.dumps(F.FIXTURE) for x in QAI_IDS) and F.FIXTURE["prefix"] != "QAI")
try: F.complete_body("gid://shopify/DraftOrder/1613906051446"); ok = False
except F.Refused: ok = True
chk("complete_body refuses a retired QAI draft id (#D28)", ok)
try: F.complete_body("gid://shopify/DraftOrder/1613611073910"); ok = False
except F.Refused: ok = True
chk("complete_body refuses a QAH (completed) draft id", ok)
try: F.complete_body("gid://shopify/DraftOrder/1"); ok = False
except F.Refused: ok = True
chk("complete_body refuses any id outside the four", ok)
chk("complete_body payload is exactly {query: MUTATION, variables: {id}}", json.loads(F.complete_body(F.TARGET_IDS[0])) == {"query": F.MUTATION, "variables": {"id": F.TARGET_IDS[0]}})
r = subprocess.run([sys.executable, "-B", os.path.join(D, "fire.py"), "live"], capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
chk("CLI: 'live' with no confirmation and no credentials is refused with exit 2 before any network", r.returncode == 2 and "REFUSED" in r.stdout and "confirm" in r.stdout)
r = subprocess.run([sys.executable, "-B", os.path.join(D, "fire.py"), "--help"], capture_output=True, text=True)
chk("CLI: no option can change host, shop, API version or target ids", not any(x in r.stdout for x in ("--shop", "--host", "--id", "--draft", "--url")))

# ---- 2. the failure model itself, and the failed 30 Sep evidence --------------------------------------------------------------------------
w = World(); w.idle_limit = 12.0; c = FConn(w, 1)
w.clock.sleep(5); ok_short = True
try: c._alive()
except ssl.SSLEOFError: ok_short = False
w.clock.sleep(10)
try: c._alive(); dies = False
except ssl.SSLEOFError: dies = True
chk("transport model: a connection survives a short idle (5 s) and dies after the simulated threshold (15 s > 12 s) with SSLEOFError", ok_short and dies)


def legacy_fire(w, sess, fire_at_s):
    """The OLD architecture (30 Sep): open the four connections first, wait for the release time, then send. Kept only to reproduce the failure."""
    tx = FTx(w); conns = [tx.open() for _ in range(4)]
    F.wait_until(w.clock, fire_at_s)
    out = []
    for i, c in enumerate(conns):
        try: recv = c.send_phase("POST", F.GRAPHQL_PATH, F.complete_body(F.TARGET_IDS[i]), sess.headers()); recv(); out.append("sent")
        except Exception as e: out.append(type(e).__name__)
    return out


w = World(); w.idle_limit = 12.0; sess = F.Session(FTx(w), ENV)
res_old = legacy_fire(w, sess, w.clock.wall() + 26)
chk("REGRESSION REPRODUCED: old architecture (open, wait 26 s, send) fails all four with SSLEOFError when idle expiry is 12 s", res_old == ["SSLEOFError"] * 4 and not w.mutations, res_old)
w = World(); w.idle_limit = 12.0; sess = F.Session(FTx(w), ENV)
chk("old architecture works only when the wait is short (5 s), which is why dry runs and the offline tests missed it", legacy_fire(w, sess, w.clock.wall() + 5) == ["sent"] * 4)
old_ev = json.load(open(os.path.join(EVID, "fire-live-evidence.json")))
s = F.evaluate_evidence(old_ev)
chk("the real failed 30 Sep evidence evaluates to 4 attempts, 0 confirmed deliveries, 0 Shopify request ids, concurrency NOT PROVEN",
    s["attempts_started"] == 4 and s["delivery_confirmed"] == 0 and s["shopify_request_ids_received"] == 0 and s["http_responses_received"] == 0 and s["mutation_responses_received"] == 0
    and s["concurrency_verdict"] == "NOT_PROVEN" and s["shopify_concurrency_evidence"] == "NONE" and s["client_overlap_confirmed"]["peak_in_flight"] == 0, s)
chk("the old evidence's own 'peak_in_flight 4' is only the diagnostic attempt overlap and is not counted as proof", s["client_overlap_attempts_DIAGNOSTIC_ONLY"]["peak_in_flight"] == 4 and s["client_overlap_confirmed"]["all_four_overlap"] is False)
rr = subprocess.run([sys.executable, "-B", os.path.join(D, "fire.py"), "evaluate", "--evidence", os.path.join(EVID, "fire-live-evidence.json")], capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
chk("CLI: `fire.py evaluate` re-reads the failed evidence offline (no credentials, no network) and prints NOT_PROVEN", rr.returncode == 0 and "NOT_PROVEN" in rr.stdout and '"delivery_confirmed": 0' in rr.stdout, rr.stdout[-200:] + rr.stderr[-200:])
man = [l.split("  ", 1) for l in open(os.path.join(EVID, "MANIFEST.sha256")).read().splitlines() if l.strip()]
bad = [n for h, n in man if hashlib.sha256(open(os.path.join(EVID, n), "rb").read()).hexdigest() != h]
chk("preserved failed-S4 evidence: every file matches MANIFEST.sha256 (immutability check)", len(man) >= 40 and not bad, bad)
chk("preserved evidence: the marker copy and the as-run script are present and the script hash equals the one recorded in the evidence",
    os.path.exists(os.path.join(EVID, "marker-fire-15905304379766.done.copy")) and hashlib.sha256(open(os.path.join(EVID, "fire.py.as-run"), "rb").read()).hexdigest() == old_ev["script_sha256"])

# ---- 3. validate mode ------------------------------------------------------------------------------------------------------------------------
rc, out, w, cf, sd = run("validate")
chk("validate: passes on the good fixture, exit 0, zero mutations, only the fixed host/paths", rc == 0 and "VALIDATE OK" in out and not w.mutations and all(x["host"] == F.SHOP and x["path"] in (F.GRAPHQL_PATH, F.TOKEN_PATH) for x in w.log), out)


def refused(name, mutate, mode="validate", extra=(), expect=None, **over):
    w = World(); mutate(w) if mutate else None
    rc, out, w, cf, sd = run(mode, w=w, extra=extra, **over)
    chk(f"refuses: {name}", rc == 2 and "REFUSED" in out and not w.mutations and (expect is None or expect in out), out[-200:])


refused("token scope wider than write_draft_orders", lambda w: setattr(w, "scope", "write_draft_orders,write_products"))
refused("effective scope outside the allowed pair", lambda w: setattr(w, "scopes", ["write_draft_orders", "read_draft_orders", "write_inventory"]))
refused("wrong app", lambda w: setattr(w, "app", "Greenside Entry Allocator"))
refused("wrong shop domain", lambda w: setattr(w, "shop", "other.myshopify.com"))
refused("draftOrderComplete signature changed", lambda w: setattr(w, "schema_args", ["id", "somethingNew"]))
def _d(w, k, v): w.drafts[F.TARGET_IDS[2]][k] = v
refused("a draft is already COMPLETED", lambda w: _d(w, "status", "COMPLETED"), expect=NM[2])
refused("a draft quantity differs", lambda w: _d(w, "qty", 3))
refused("a draft total differs", lambda w: _d(w, "amount", "9.99"))
refused("a draft tag is missing", lambda w: _d(w, "tags", ["qa-stress"]))
refused("a draft is for the QAH product title", lambda w: _d(w, "title", "QA — Allocator Stress Test"))
refused("a draft currency differs", lambda w: _d(w, "currencyCode", "USD"))
refused("a draft is missing", lambda w: w.drafts.pop(F.TARGET_IDS[3]))
refused("an extra OPEN draft carrying the fixture tag exists", lambda w: w.extra_list.append({"id": "gid://shopify/DraftOrder/999", "name": "#D99", "status": "OPEN", "tags": [F.FIXTURE["prefix"]]}))
refused("stress.json points at another competition", None, cfg={"competition_id": "15903096668534"})
refused("stress.json prefix is QAH", None, cfg={"prefix": "QAH"})
refused("stress.json drafts differ", None, cfg={"draft_orders": F.FIXTURE["drafts"][:3]})
refused("credentials missing", None, env={})

# ---- 4. retired fixture and one-shot state (no bypass) ---------------------------------------------------------------------------------------
REAL_RETIRED = dict(F.RETIRED_COMPETITIONS)
F.RETIRED_COMPETITIONS = {**REAL_RETIRED, F.FIXTURE["competition_id"]: "retired for this test"}
rc, out, w, cf, sd = run("live", extra=LIVE)
chk("RETIRED: a live fire on a retired fixture is refused before any network, with no marker file needed", rc == 2 and "retired" in out and w.opened == 0 and not w.mutations and not os.path.exists(f"{sd}/fire-{F.FIXTURE['competition_id']}.done"), out)
F.RETIRED_COMPETITIONS = REAL_RETIRED                            # the real list: QAI retired, the pinned fresh fixture live-capable
w = World(); sd = state_dir(w); open(f"{sd}/fire-{F.FIXTURE['competition_id']}.done", "w").write("old marker")
rc, out, w, cf, _ = run("live", w=w, sd=sd, extra=LIVE)
chk("one-shot: an existing marker refuses the run before any network (opened 0, zero mutations); the marker file is left untouched", rc == 2 and "already attempted" in out and w.opened == 0 and open(f"{sd}/fire-{F.FIXTURE['competition_id']}.done").read() == "old marker", out)
w = World(); sd = state_dir(w); mk = f"{sd}/m.done"; open(mk, "w").write("x")
fs = F.Session(FTx(w), ENV)
try: F.fire(FTx(w), fs, "live", w.clock, None, mk); ab = None
except F.FireAborted as e: ab = e.reason
chk("one-shot: a marker that appears after the pre-checks aborts the fire at the release (exclusive create), zero mutations", ab is not None and "marker" in ab and not w.mutations and open(mk).read() == "x", ab)

# ---- 5. dry mode: same machinery, read-only --------------------------------------------------------------------------------------------------
rc, out, w, cf, sd = run("dry")
ev = ev_of(sd); S = ev["evidence_summary"]
chk("dry: exit 0, four READ_OK, zero mutations, four connections opened after the checks", rc == 0 and [r["result"] for r in ev["requests"]] == ["READ_OK"] * 4 and not w.mutations and w.opened == 12, (out, w.opened))
chk("dry: the pre-release probe and the second request used the SAME connection for each draft (one connection per draft, two requests each)",
    all(len({x["idx"] for x in w.log if x["idx"] >= 9 and json.loads(x["body"]).get("variables", {}).get("id") == t}) == 1 for t in F.TARGET_IDS))
chk("dry: delivery confirmed 4/4 with 4 distinct request ids, client overlap of confirmed requests is all four", S["delivery_confirmed"] == 4 and S["distinct_shopify_request_ids"] == 4 and S["client_overlap_confirmed"]["all_four_overlap"] and w.peak == 4, S)
need = {"n", "target_id", "name", "qty", "operation", "result", "attempt_started", "connection_established", "probe_ok", "send_started", "send_completed", "http_response_received",
        "shopify_request_id", "mutation_response_received", "delivery_confirmed", "release_utc", "t_release_ms_after_main_release", "t_send_ms_after_main_release",
        "t_sent_ms_after_main_release", "t_headers_ms_after_main_release", "t_done_ms_after_main_release", "duration_ms", "http_status", "request_id", "throttle",
        "graphql_errors", "user_errors", "draft_status_after", "connection_age_ms_at_release", "connection_idle_ms_at_release", "probe_request_id"}
chk("evidence: every request records attempt/send/response/request-id/delivery flags, connection age and idle at release, probe and timing fields", all(need <= set(r) for r in ev["requests"]), [need - set(r) for r in ev["requests"]])
chk("evidence: connection age at release is recorded and within the machine-checkable limits", all(r["connection_age_ms_at_release"] <= F.MAX_CONN_AGE_S * 1000 and r["connection_idle_ms_at_release"] <= F.MAX_CONN_IDLE_S * 1000 for r in ev["requests"]) and ev["transport_limits"]["max_conn_age_s"] == F.MAX_CONN_AGE_S)
chk("evidence: the summary distinguishes attempts, sends, responses, request ids, mutation responses and confirmed deliveries",
    {"attempts_started", "send_started", "http_responses_received", "shopify_request_ids_received", "mutation_responses_received", "delivery_confirmed", "client_overlap_confirmed",
     "client_overlap_attempts_DIAGNOSTIC_ONLY", "shopify_concurrency_evidence", "concurrency_verdict"} <= set(S))
chk("evidence: per-request timestamps are ordered connect <= probe done <= release <= send <= sent <= headers <= done",
    all(r["t_connected_ms_after_main_release"] <= r["t_probe_done_ms_after_main_release"] <= r["t_release_ms_after_main_release"] <= r["t_send_ms_after_main_release"] <= r["t_sent_ms_after_main_release"] <= r["t_headers_ms_after_main_release"] <= r["t_done_ms_after_main_release"] for r in ev["requests"]))
blob = open(sd + "/evidence.json").read() + out
chk("secrets: token, client id/secret and Cloudflare token never appear in evidence or output", not any(s in blob for s in (TOKEN, CID, CSEC, CFTOK)))

# ---- 6. repaired architecture under the SAME long wait that broke the old one ------------------------------------------------------------------
for secs in (26, 600):
    w = World(); w.idle_limit = 12.0; fat = fa(w, secs); fire_at_s = int(fat[1]) / 1000
    rc, out, w, cf, sd = run("dry", w=w, extra=fat)
    ev = ev_of(sd); S = ev["evidence_summary"]
    late = w.opens[-4:]
    chk(f"REPAIRED: with a {secs}s wait and a 12 s idle expiry the fire succeeds 4/4 (the old architecture failed 4/4 at 26 s)", rc == 0 and S["delivery_confirmed"] == 4 and S["send_started"] == 4, out[-300:])
    chk(f"REPAIRED ({secs}s): no mutation connection exists earlier than PRE_LEAD_S before the release", w.opened == 12 and all(t >= fire_at_s - F.PRE_LEAD_S - 0.5 for t in late), [round(t - fire_at_s, 1) for t in late])
    chk(f"REPAIRED ({secs}s): connection age at release <= {F.MAX_CONN_AGE_S}s and idle <= {F.MAX_CONN_IDLE_S}s for all four", all(r["connection_age_ms_at_release"] <= F.MAX_CONN_AGE_S * 1000 and r["connection_idle_ms_at_release"] <= F.MAX_CONN_IDLE_S * 1000 for r in ev["requests"]), [r["connection_age_ms_at_release"] for r in ev["requests"]])
w = World(); w.idle_limit = 12.0
rc, out, w, cf, sd = run("dry", w=w)
chk("REPAIRED: with no --fire-at (immediate release) the fire succeeds", rc == 0 and ev_of(sd)["evidence_summary"]["delivery_confirmed"] == 4)
w = World(); w.idle_limit = 12.0; fat = fa(w, 40)
rc, out, w, cf, sd = run("live", w=w, extra=LIVE + fat)
ev = ev_of(sd); S = ev["evidence_summary"]
chk("REPAIRED live path (fake) with a 40 s wait and 12 s idle expiry: 4/4 COMPLETED, 4 distinct request ids, exactly four mutation requests", rc == 0 and [r["result"] for r in ev["requests"]] == ["COMPLETED"] * 4 and len(w.mutations) == 4 and S["distinct_shopify_request_ids"] == 4, out[-300:])
chk("live: one mutation per fixture draft, each body exactly {query: MUTATION, variables: {id}}, sent on the same connection as its probe", sorted(m["variables"]["id"] for m in w.mutations) == sorted(F.TARGET_IDS) and all(m == {"query": F.MUTATION, "variables": {"id": m["variables"]["id"]}} for m in w.mutations)
    and all(len({x["idx"] for x in w.log if x["idx"] >= 9 and json.loads(x["body"]).get("variables", {}).get("id") == t}) == 1 for t in F.TARGET_IDS))
muts = [x for x in w.log if x["path"] == F.GRAPHQL_PATH and "CompleteQaiDraft" in x["body"]]
chk("live: mutation requests carry only Content-Type and X-Shopify-Access-Token to the fixed host/path", all(x["headers"] == ["Content-Type", "X-Shopify-Access-Token"] and x["host"] == F.SHOP for x in muts))
chk("live: verdict is only CLIENT_SIDE_DELIVERY_OVERLAP_ONLY and states that server-side proof needs overlap.py and four orders", S["concurrency_verdict"] == "CLIENT_SIDE_DELIVERY_OVERLAP_ONLY" and "overlap.py" in S["shopify_concurrency_evidence"] and "not sufficient" in S["shopify_concurrency_evidence"], S)
chk("live: released from one barrier (send spread < 50 ms), client and server peak in flight 4", S["client_overlap_confirmed"]["peak_in_flight"] == 4 and w.peak == 4 and max(r["t_send_ms_after_main_release"] for r in ev["requests"]) - min(r["t_send_ms_after_main_release"] for r in ev["requests"]) < 50)
chk("live: the fired marker exists and a second live run in the same state directory is refused with zero further mutations",
    os.path.exists(f"{sd}/fire-{F.FIXTURE['competition_id']}.done") and run("live", w=World(), sd=sd, extra=LIVE)[0] == 2 and len(w.mutations) == 4)

# ---- 7. pre-release gates: abort the whole fire before any mutation -----------------------------------------------------------------------------
def aborted(name, mut, expect, mode="live"):
    w = World(); mut(w)
    rc, out, w, cf, sd = run(mode, w=w, extra=LIVE if mode == "live" else ())
    e = ev_of(sd)
    ok = rc == 2 and "ABORTED BEFORE ANY MUTATION" in out and expect in out and not w.mutations and e["evidence_summary"]["send_started"] == 0 and e["evidence_summary"]["delivery_confirmed"] == 0 and w.opened == 12 \
        and not os.path.exists(f"{sd}/fire-{F.FIXTURE['competition_id']}.done")
    chk(f"abort before any mutation: {name}", ok, out[-250:])


aborted("a probe returns HTTP 500", lambda w: w.probe_fail.update({F.TARGET_IDS[1]: "http500"}), NM[1])
aborted("a probe raises (timeout)", lambda w: w.probe_fail.update({F.TARGET_IDS[2]: "exception"}), "TimeoutError")
aborted("a probe response has no Shopify request id", lambda w: w.probe_fail.update({F.TARGET_IDS[0]: "noid"}), NM[0])
aborted("a probe shows the draft is no longer OPEN", lambda w: w.probe_fail.update({F.TARGET_IDS[3]: "notopen"}), NM[3])
aborted("one connection is not open before the barrier", lambda w: setattr(w, "closed_conn_index", 10), "RuntimeError")
aborted("a connection is broken for requests but still reports open (the probe must run on the connection that will send)", lambda w: w.broken.add(11), "SSLEOFError")
aborted("one connection is older than MAX_CONN_AGE_S at release (slow probe, 6 s)", lambda w: w.probe_sim_delay.update({F.TARGET_IDS[2]: 6.0}), "too old")
w = World(); old = (F.PRE_LEAD_S, F.MAX_CONN_AGE_S, F.MAX_CONN_IDLE_S); F.PRE_LEAD_S, F.MAX_CONN_AGE_S, F.MAX_CONN_IDLE_S = 4.5, 6.0, 4.0
try: rc, out, w, cf, sd = run("live", w=w, extra=LIVE + fa(w, 30))
finally: F.PRE_LEAD_S, F.MAX_CONN_AGE_S, F.MAX_CONN_IDLE_S = old
chk("abort before any mutation: idle gate alone (age 4.5 s <= 6 s but idle 4.5 s > 4 s) refuses the release", rc == 2 and "too old" in out and "idle 4." in out and not w.mutations and w.opened == 12, out[-250:])
w = World(); old = (F.PRE_LEAD_S, F.MAX_CONN_AGE_S, F.MAX_CONN_IDLE_S); F.PRE_LEAD_S, F.MAX_CONN_AGE_S, F.MAX_CONN_IDLE_S = 3.0, 0.5, 4.0
try: rc, out, w, cf, sd = run("live", w=w, extra=LIVE + fa(w, 30))
finally: F.PRE_LEAD_S, F.MAX_CONN_AGE_S, F.MAX_CONN_IDLE_S = old
chk("abort before any mutation: age gate alone (idle 3 s <= 4 s but age 3 s > 0.5 s) refuses the release", rc == 2 and "too old" in out and not w.mutations and w.opened == 12, out[-250:])
w = World(); w.probe_sim_delay[F.TARGET_IDS[1]] = 3.5
rc, out, w, cf, sd = run("live", w=w, extra=LIVE + fa(w, 1))
chk("abort before any mutation: release would slip more than MAX_SLIP_S past --fire-at (open+probe took 3.5 s, fire-at was 1 s away)", rc == 2 and "slip" in out and not w.mutations and w.opened == 12, out[-250:])
w = World(); w.probe_fail[F.TARGET_IDS[0]] = "http500"
rc, out, w, cf, sd = run("live", w=w, extra=LIVE)
chk("abort: three healthy connections are not reused or reopened after a failed probe (still 12 opens, zero mutations, no retries)", rc == 2 and w.opened == 12 and not w.mutations)
chk("abort: the failed fire is recorded in evidence (aborted_before_release) with 0 deliveries and verdict NOT_PROVEN", ev_of(sd)["aborted_before_release"] and ev_of(sd)["evidence_summary"]["concurrency_verdict"] == "NOT_PROVEN")

# ---- 8. evidence semantics: attempts are not deliveries, SSLEOF is not delivery ------------------------------------------------------------------
w = World(); w.kill_after_probe = set(F.TARGET_IDS)
rc, out, w, cf, sd = run("live", w=w, extra=LIVE)
ev = ev_of(sd); S = ev["evidence_summary"]
chk("SSLEOF on all four sends (connections die after the probe): 4 attempts, 4 send_started, 0 send_completed, 0 responses, 0 request ids, 0 deliveries, NOT_PROVEN, exit 3",
    rc == 3 and S["attempts_started"] == 4 and S["send_started"] == 4 and S["send_completed"] == 0 and S["http_responses_received"] == 0 and S["shopify_request_ids_received"] == 0
    and S["delivery_confirmed"] == 0 and S["concurrency_verdict"] == "NOT_PROVEN" and S["shopify_concurrency_evidence"] == "NONE" and not w.mutations and all(r["result"] == "UNKNOWN" and r["error"] == "SSLEOFError" for r in ev["requests"]), (S, out[-200:]))
chk("SSLEOF: the diagnostic attempt overlap may show 4 but the confirmed overlap is 0 and the printed verdict says Concurrency NOT PROVEN", S["client_overlap_attempts_DIAGNOSTIC_ONLY"]["peak_in_flight"] >= 1 and S["client_overlap_confirmed"]["peak_in_flight"] == 0 and "Concurrency NOT PROVEN" in out and "deliveries confirmed 0/4" in out, out[-200:])
w = World(); w.kill_after_probe = {F.TARGET_IDS[1]}
rc, out, w, cf, sd = run("live", w=w, extra=LIVE)
ev = ev_of(sd); S = ev["evidence_summary"]
chk("one SSLEOF among four: 3 confirmed deliveries, 1 UNKNOWN, exactly 3 mutation requests reached the server, no retry, verdict NOT_PROVEN, exit 3",
    rc == 3 and S["delivery_confirmed"] == 3 and len(w.mutations) == 3 and S["concurrency_verdict"] == "NOT_PROVEN" and [r["result"] for r in ev["requests"]].count("UNKNOWN") == 1 and w.opened == 12)
w = World(); w.no_request_id = True
rc, out, w, cf, sd = run("live", w=w, extra=LIVE)
S = ev_of(sd)["evidence_summary"]
chk("HTTP 200 responses WITHOUT a Shopify request id are not confirmed deliveries: 0 confirmed, 0 ids, NOT_PROVEN, exit 3", rc == 3 and S["http_responses_received"] == 4 and S["shopify_request_ids_received"] == 0 and S["delivery_confirmed"] == 0 and S["concurrency_verdict"] == "NOT_PROVEN", S)
w = World(); w.same_request_id = True
rc, out, w, cf, sd = run("live", w=w, extra=LIVE)
S = ev_of(sd)["evidence_summary"]
chk("four deliveries sharing one request id: distinct ids 1, verdict stays NOT_PROVEN", S["delivery_confirmed"] == 4 and S["distinct_shopify_request_ids"] == 1 and S["concurrency_verdict"] == "NOT_PROVEN", S)
w = World(); w.fail = {F.TARGET_IDS[1]: "http500", F.TARGET_IDS[2]: "usererror", F.TARGET_IDS[3]: "exception"}
rc, out, w, cf, sd = run("live", w=w, extra=LIVE)
ev = ev_of(sd); by = {r["name"]: r for r in ev["requests"]}
chk("failure outcomes: one COMPLETED, one HTTP error and one userError (both delivered: they carry request ids), one exception (UNKNOWN, not delivered), exit 3",
    rc == 3 and by[NM[0]]["result"] == "COMPLETED" and by[NM[1]]["result"] == "ERROR" and by[NM[1]]["delivery_confirmed"] and by[NM[2]]["result"] == "ERROR" and by[NM[2]]["user_errors"]
    and by[NM[3]]["result"] == "UNKNOWN" and by[NM[3]]["delivery_confirmed"] is False and by[NM[3]]["error"] == "TimeoutError", out)
chk("failure: no retries, still exactly four mutation requests, 'DO NOT RE-RUN' printed, marker present", len(w.mutations) == 4 and len({m["variables"]["id"] for m in w.mutations}) == 4 and "DO NOT RE-RUN" in out and os.path.exists(f"{sd}/fire-{F.FIXTURE['competition_id']}.done"))
w = World(); w.fail = {F.TARGET_IDS[0]: "leak"}; leaked = None; sd_leak = state_dir(w)
try: run("live", w=w, extra=LIVE, sd=sd_leak)
except SystemExit as e: leaked = str(e)
chk("secrets: if a server response echoes the access token into the evidence, the run aborts and writes no evidence file", leaked is not None and "credential appeared" in leaked and TOKEN not in leaked and not os.path.exists(sd_leak + "/evidence.json"), leaked)

# ---- 9. live gates and arguments ------------------------------------------------------------------------------------------------------------------
def live_refused(name, w=None, cf=None, extra=LIVE, expect=None, network=True, **over):
    rc, out, w, cf, sd = run("live", w=w, cf=cf, extra=extra, **over)
    chk(f"live refuses: {name}", rc == 2 and "REFUSED" in out and not w.mutations and (expect is None or expect in out) and (network or w.opened == 0), out[-250:])


live_refused("no --confirm", extra=(), expect="confirm", network=False)
live_refused("wrong --confirm phrase", extra=("--confirm", "yes"), network=False)
cf = FCF(); cf.dry = "true"; live_refused("QA Worker DRY_RUN is true (gate not passed)", cf=cf, expect="DRY_RUN")
cf = FCF(); cf.prod_present = True; live_refused("production Worker exists", cf=cf, expect="production Worker")
cf = FCF(); cf.version = "other-version"; live_refused("QA Worker not on the gate-passed version", cf=cf, expect="gate-passed")
cf = FCF(); cf.pct = 50; live_refused("QA Worker version split", cf=cf)
live_refused("gate.txt missing", **{"gate.txt": None}, expect="gate.txt")
live_refused("gate.txt not PASSED", **{"gate.txt": "GATE_FAILED"})
live_refused("arming time missing", **{"qag-start": None}, expect="arming")
w = World(); live_refused("arming older than 1200 s", w=w, **{"qag-start": str(int(w.clock.wall() * 1000) - 1300_000)}, expect="old")
live_refused("guard not running", **{"guard.pid": "999999999"}, expect="guard")
w = World(); sd = state_dir(w); open(sd + "/manual-stop", "w").write("x"); live_refused("manual-stop present", w=w, sd=sd, expect="manual-stop")
live_refused("Cloudflare credentials missing", env={k: v for k, v in ENV.items() if not k.startswith("CLOUDFLARE")}, expect="CLOUDFLARE")
w = World(); live_refused("--fire-at in the past", w=w, extra=LIVE + ["--fire-at", str(int(w.clock.wall() * 1000) - 60_000)], expect="fire-at", network=False)
w = World(); live_refused("--fire-at more than 30 minutes ahead", w=w, extra=LIVE + ["--fire-at", str(int(w.clock.wall() * 1000) + 3_600_000)], expect="fire-at", network=False)
w = World(); w.drafts[F.TARGET_IDS[0]]["status"] = "COMPLETED"; live_refused("a draft is not OPEN (precondition)", w=w)

n, m = sum(RES), len(RES)
print(f"FIRE SELF-TEST: {n}/{m} passed")
sys.exit(0 if n == m else 1)
