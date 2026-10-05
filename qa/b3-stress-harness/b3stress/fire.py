#!/usr/bin/env python3
"""B3 S4 fire script: exactly four concurrent draftOrderComplete calls for the pinned fixture (currently QAJ D32-D35; QAI D28-D31 is retired).

Modes
  validate  read-only. Checks config, app identity, scopes, schema and the four drafts. Never builds or sends the mutation.
  dry       validate, then the same four-thread barrier machinery using a READ-ONLY query (draftOrder id/name/status) per draft.
  live      validate, then the live gates (QA Worker armed, production Worker absent, arming fresh), then the four mutations.
            Refused unless --confirm carries the exact phrase, the fixture is not retired and the one-shot marker is absent.
            Needs explicit approval. Never run without it.
  evaluate  offline: re-evaluate an evidence JSON (--evidence FILE) under the corrected evidence semantics. No network, no credentials.

Usage  fire.py MODE [--out FILE] [--state-dir DIR] [--fire-at EPOCH_MS] [--confirm PHRASE]
Exit   0 all four requests delivered and succeeded | 2 refused or aborted before any mutation was sent | 3 mutations released, at least one not confirmed

Reads QA_STRESS_SHOPIFY_CLIENT_ID / QA_STRESS_SHOPIFY_CLIENT_SECRET (client-credentials grant, token in memory only) and, for the
live gates, CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID (read-only GETs). Prints and writes no credential or token.
See FIRE.md for the endpoint, scope, payload, concurrency and failure contract.
"""
import argparse, datetime, hashlib, http.client, json, os, ssl, sys, threading, time, urllib.parse

# ---- fixed target: nothing below is configurable from the command line or environment -------------------------------------------
SHOP = "7r5csb-1j.myshopify.com"          # QA and production share this store; QA separation is the fixture + the QA Worker
API_VERSION = "2025-10"
GRAPHQL_PATH = f"/admin/api/{API_VERSION}/graphql.json"
TOKEN_PATH = "/admin/oauth/access_token"
APP_NAME = "Greenside QA Stress Driver"
REQUIRED_SCOPE = "write_draft_orders"
ALLOWED_SCOPES = {"write_draft_orders", "read_draft_orders"}     # read_ is implied by write_ (S1 finding)
CF_HOST = "api.cloudflare.com"
QA_WORKER = "greenside-entry-allocator-qa"
PROD_WORKER = "greenside-entry-allocator"
CONFIRM_PHRASE = "FIRE-QAJ-D32-D35-4-COMPLETIONS"
FIXTURE = {
    "competition_id": "15915106926966", "product_gid": "gid://shopify/Product/15915106926966", "prefix": "QAJ",
    "variant_gid": "gid://shopify/ProductVariant/58894787314038", "capacity": 6, "order_quantities": [1, 1, 2, 2],
    "line_title": "QA — Allocator Stress Test C", "currency": "GBP", "unit_price": "1.00", "tags": ["QAJ", "qa-stress"],
    "drafts": [
        {"name": "#D32", "id": "gid://shopify/DraftOrder/1614665220470", "qty": 1},
        {"name": "#D33", "id": "gid://shopify/DraftOrder/1614665253238", "qty": 1},
        {"name": "#D34", "id": "gid://shopify/DraftOrder/1614665286006", "qty": 2},
        {"name": "#D35", "id": "gid://shopify/DraftOrder/1614665318774", "qty": 2},
    ],
}
TARGET_IDS = [d["id"] for d in FIXTURE["drafts"]]
N = len(TARGET_IDS)

# ---- the only three GraphQL documents this script can send after the token exchange -------------------------------------------
# Selection sets are limited to fields the Stress Driver (write_draft_orders only) can read; `order`, `variant` and `product` would
# be ACCESS_DENIED for it. paymentPending is deprecated (default false) and deliberately omitted.
MUTATION = ("mutation CompleteQaiDraft($id: ID!) { draftOrderComplete(id: $id) { draftOrder { id name status } "
            "userErrors { field message } } }")
PROBE = "query ProbeQaiDraft($id: ID!) { draftOrder(id: $id) { id name status } }"
CHECK_DRAFT = ("query CheckQaiDraft($id: ID!) { draftOrder(id: $id) { id name status tags currencyCode totalPriceSet { shopMoney "
               "{ amount currencyCode } } lineItems(first: 5) { nodes { quantity title } } } }")
CHECK_APP = "{ shop { myshopifyDomain } currentAppInstallation { app { title } accessScopes { handle } } }"
CHECK_SCHEMA = ('{ m: __type(name: "Mutation") { fields { name args(includeDeprecated: true) { name type { kind name ofType { name } } } '
                'type { name } } } p: __type(name: "DraftOrderCompletePayload") { fields { name } } }')
CHECK_LIST = "{ draftOrders(first: 10, sortKey: ID, reverse: true) { nodes { id name status tags } } }"
ALLOWED_DOCUMENTS = {MUTATION, PROBE, CHECK_DRAFT, CHECK_APP, CHECK_SCHEMA, CHECK_LIST}

MAX_FIRE_AHEAD_MS = 30 * 60 * 1000
ARMING_MAX_AGE_S = 1200          # goliveb/guard deadline: no order within 1200 s of arming
REQUEST_TIMEOUT_S = 60
READY_TIMEOUT_S = 20
# Transport invariant (S4 postmortem, 2026-09-30): connections through this container's proxy survived 10 s idle and were dead at 20 s.
# No mutation connection may exist until PRE_LEAD_S before the release; the release is refused if any connection is older than
# MAX_CONN_AGE_S or idle longer than MAX_CONN_IDLE_S. firetest.py asserts these stay well inside the observed limits.
PROXY_IDLE_OBSERVED_SURVIVED_S = 10
PROXY_IDLE_OBSERVED_DEAD_S = 20
PRE_LEAD_S = 3.0
MAX_CONN_AGE_S = 5.0
MAX_CONN_IDLE_S = 4.0
MAX_SLIP_S = 2.0
# Fixtures whose one-shot state is consumed. A live fire on a retired competition is refused whether or not its marker file exists.
RETIRED_COMPETITIONS = {"15905304379766": "QAI: S4 live fire of 2026-09-30 failed (transport); evidence preserved in qa/b3-stress-harness/evidence/s4-2026-09-30-failed/"}


class Refused(Exception):
    """Raised before any mutation or probe request is sent."""


# ---- transports (host is fixed per class; there is no host parameter anywhere) -------------------------------------------------
class _Conn:
    def __init__(self, raw):
        self.raw = raw

    def is_open(self):
        return getattr(self.raw, "sock", None) is not None

    def request(self, method, path, body, headers):
        self.raw.request(method, path, body, headers)
        r = self.raw.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()

    def send_phase(self, method, path, body, headers):
        """Split form used by fire(): (send, receive_headers, read_body) so each phase can be timestamped."""
        self.raw.request(method, path, body, headers)
        return lambda: self.raw.getresponse()

    def close(self):
        try:
            self.raw.close()
        except Exception:
            pass


class _ProxyTransport:
    HOST = None

    def open(self):
        px = urllib.parse.urlparse(os.environ["HTTPS_PROXY"])
        ca = "/root/.ccr/ca-bundle.crt"
        ctx = ssl.create_default_context(cafile=ca if os.path.exists(ca) else None)
        hdr = {}
        if px.username:
            import base64
            cred = f"{urllib.parse.unquote(px.username)}:{urllib.parse.unquote(px.password or '')}".encode()
            hdr["Proxy-Authorization"] = "Basic " + base64.b64encode(cred).decode()
        c = http.client.HTTPSConnection(px.hostname, px.port, context=ctx, timeout=REQUEST_TIMEOUT_S)
        c.set_tunnel(self.HOST, 443, headers=hdr)
        c.connect()
        return _Conn(c)


class ShopifyTransport(_ProxyTransport):
    HOST = SHOP


class CloudflareTransport(_ProxyTransport):
    HOST = CF_HOST


# ---- helpers ------------------------------------------------------------------------------------------------------------------
def iso(ns):
    return datetime.datetime.fromtimestamp(ns / 1e9, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Session:
    """One Shopify access token, held in memory only."""

    def __init__(self, tx, env):
        self.tx, self.token, self.scope, self.secrets = tx, None, None, []
        cid, sec = env.get("QA_STRESS_SHOPIFY_CLIENT_ID"), env.get("QA_STRESS_SHOPIFY_CLIENT_SECRET")
        if not cid or not sec:
            raise Refused("QA_STRESS_SHOPIFY_CLIENT_ID / QA_STRESS_SHOPIFY_CLIENT_SECRET not set")
        self.secrets += [cid, sec]
        c = tx.open()
        try:
            st, _, raw = c.request("POST", TOKEN_PATH, urllib.parse.urlencode(
                {"client_id": cid, "client_secret": sec, "grant_type": "client_credentials"}),
                {"Content-Type": "application/x-www-form-urlencoded"})
        finally:
            c.close()
        try:
            j = json.loads(raw)
        except Exception:
            raise Refused(f"token exchange returned HTTP {st} with a non-JSON body")
        if st != 200 or not j.get("access_token"):
            raise Refused(f"token exchange failed: HTTP {st}")
        self.token, self.scope = j["access_token"], j.get("scope")
        self.secrets.append(self.token)

    def headers(self):
        return {"Content-Type": "application/json", "X-Shopify-Access-Token": self.token}

    def body(self, document, variables=None):
        if document not in ALLOWED_DOCUMENTS:
            raise Refused("document not in the allow-list")
        d = {"query": document}
        if variables is not None:
            d["variables"] = variables
        return json.dumps(d)

    def read(self, document, variables=None):
        """Read-only query on a short-lived connection. Never accepts MUTATION."""
        if document == MUTATION:
            raise Refused("read() cannot send the mutation")
        c = self.tx.open()
        try:
            st, _, raw = c.request("POST", GRAPHQL_PATH, self.body(document, variables), self.headers())
        finally:
            c.close()
        try:
            return st, json.loads(raw)
        except Exception:
            return st, {"errors": [{"message": "non-JSON response"}]}


def complete_body(draft_id):
    """The one and only mutation payload. The id must be one of the four fixture drafts."""
    if draft_id not in TARGET_IDS:
        raise Refused(f"{draft_id} is not one of the four QAI fixture drafts")
    return json.dumps({"query": MUTATION, "variables": {"id": draft_id}})


# ---- preconditions (read-only; everything the Stress Driver can see) ----------------------------------------------------------
def check_config(state_dir):
    fails = []
    try:
        cfg = json.load(open(os.path.join(state_dir, "stress.json")))
    except Exception as e:
        return [f"stress.json unreadable: {type(e).__name__}"]
    for k in ("competition_id", "product_gid", "prefix", "variant_gid", "capacity", "order_quantities"):
        if cfg.get(k) != FIXTURE[k]:
            fails.append(f"stress.json {k} = {cfg.get(k)!r}, expected {FIXTURE[k]!r}")
    if cfg.get("draft_orders") != FIXTURE["drafts"]:
        fails.append("stress.json draft_orders differ from the four expected drafts")
    if sum(FIXTURE["order_quantities"]) != FIXTURE["capacity"]:
        fails.append("fixture quantities do not sum to capacity")
    return fails


def check_shopify(sess):
    fails = []
    if set(str(sess.scope or "").replace(",", " ").split()) != {REQUIRED_SCOPE}:
        fails.append(f"token scope is {sess.scope!r}, expected exactly {REQUIRED_SCOPE}")
    st, d = sess.read(CHECK_APP)
    a = (d.get("data") or {}).get("currentAppInstallation") or {}
    if d.get("errors") or st != 200:
        fails.append(f"app query failed: HTTP {st}")
    if ((d.get("data") or {}).get("shop") or {}).get("myshopifyDomain") != SHOP:
        fails.append("shop is not " + SHOP)
    if (a.get("app") or {}).get("title") != APP_NAME:
        fails.append(f"app is {(a.get('app') or {}).get('title')!r}, expected {APP_NAME!r}")
    scopes = {s["handle"] for s in a.get("accessScopes") or []}
    if REQUIRED_SCOPE not in scopes or not scopes <= ALLOWED_SCOPES:
        fails.append(f"effective scopes {sorted(scopes)} not within {sorted(ALLOWED_SCOPES)}")
    st, d = sess.read(CHECK_SCHEMA)
    fs = {f["name"]: f for f in ((d.get("data") or {}).get("m") or {}).get("fields") or []}
    f = fs.get("draftOrderComplete")
    args = {x["name"]: x for x in (f or {}).get("args") or []}
    idt = (args.get("id") or {}).get("type") or {}
    if not f or "id" not in args or idt.get("kind") != "NON_NULL" or (idt.get("ofType") or {}).get("name") != "ID" \
            or not set(args) <= {"id", "paymentPending", "paymentGatewayId", "sourceName"}:
        fails.append(f"draftOrderComplete signature unexpected: {sorted(args)}")
    if {"draftOrder", "userErrors"} - {x["name"] for x in ((d.get("data") or {}).get("p") or {}).get("fields") or []}:
        fails.append("DraftOrderCompletePayload lacks draftOrder/userErrors")
    for dr in FIXTURE["drafts"]:
        st, d = sess.read(CHECK_DRAFT, {"id": dr["id"]})
        o = (d.get("data") or {}).get("draftOrder")
        if d.get("errors") or not o:
            fails.append(f"{dr['name']}: unreadable (HTTP {st}, {[e.get('message') for e in d.get('errors') or []][:1]})")
            continue
        li = (o.get("lineItems") or {}).get("nodes") or []
        m = (o.get("totalPriceSet") or {}).get("shopMoney") or {}
        want = f"{dr['qty'] * 1:.2f}"
        bad = [n for n, ok in (
            ("id", o.get("id") == dr["id"]), ("name", o.get("name") == dr["name"]), ("status", o.get("status") == "OPEN"),
            ("tags", sorted(o.get("tags") or []) == FIXTURE["tags"]),
            ("lines", len(li) == 1 and li[0].get("quantity") == dr["qty"] and li[0].get("title") == FIXTURE["line_title"]),
            ("currency", o.get("currencyCode") == FIXTURE["currency"] and m.get("currencyCode") == FIXTURE["currency"]),
            ("total", m.get("amount") is not None and abs(float(m["amount"]) - float(want)) < 0.005)) if not ok]
        if bad:
            fails.append(f"{dr['name']}: mismatch in {bad}")
    st, d = sess.read(CHECK_LIST)
    nodes = ((d.get("data") or {}).get("draftOrders") or {}).get("nodes") or []
    qai = [n for n in nodes if "QAJ" in (n.get("tags") or [])]
    if sorted(n["id"] for n in qai) != sorted(TARGET_IDS) or any(n.get("status") != "OPEN" for n in qai):
        fails.append("QAI-tagged drafts among the latest 10 are not exactly the four OPEN fixture drafts")
    return fails


def cf_json(cf, path, token):
    c = cf.open()
    try:
        st, _, raw = c.request("GET", path, None, {"Authorization": "Bearer " + token})
    finally:
        c.close()
    try:
        return st, json.loads(raw)
    except Exception:
        return st, {}


def check_live_gates(cf, env, state_dir, now_s):
    """Everything that must hold only at the moment of the live fire. Read-only GETs plus local state files."""
    fails = []
    acct = env.get("CLOUDFLARE_ACCOUNT_ID")
    if not acct or not env.get("CLOUDFLARE_API_TOKEN"):
        return ["CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN not set, cannot verify the Worker"]
    base, tok = f"/client/v4/accounts/{acct}/workers/scripts", env["CLOUDFLARE_API_TOKEN"]
    st, d = cf_json(cf, f"{base}/{PROD_WORKER}/settings", tok)
    if st != 404 and not (st == 200 and d.get("success") is False):
        fails.append(f"production Worker {PROD_WORKER} is present or unverifiable (HTTP {st}); refusing")
    st, d = cf_json(cf, f"{base}/{QA_WORKER}/deployments", tok)
    dep = ((d.get("result") or {}).get("deployments") or [{}])[0].get("versions") or []
    st2, s = cf_json(cf, f"{base}/{QA_WORKER}/settings", tok)
    dry = [b.get("text") for b in (s.get("result") or {}).get("bindings", []) if b.get("name") == "DRY_RUN"]
    if dry != ["false"]:
        fails.append(f"QA Worker DRY_RUN is {dry}, expected ['false'] (gate not passed)")

    def rd(name):
        try:
            return open(os.path.join(state_dir, name)).read().strip()
        except OSError:
            return None
    newver, gate, armed = rd("newver.txt"), rd("gate.txt"), rd("qag-start")
    if gate != "PASSED":
        fails.append(f"gate.txt is {gate!r}, expected PASSED")
    if len(dep) != 1 or dep[0].get("percentage") != 100 or not newver or dep[0].get("version_id") != newver:
        fails.append("QA Worker is not on the single 100% gate-passed version")
    try:
        age = now_s - int(armed) / 1000
        if not 0 <= age <= ARMING_MAX_AGE_S:
            fails.append(f"arming is {age:.0f}s old (must be 0..{ARMING_MAX_AGE_S}s)")
    except (TypeError, ValueError):
        fails.append("qag-start (arming time) missing")
    pid = rd("guard.pid")
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, OSError):
        fails.append("guard.sh is not running")
    if os.path.exists(os.path.join(state_dir, "manual-stop")):
        fails.append("manual-stop present")
    return fails


# ---- clock (injectable: tests simulate time and connection expiry) ----------------------------------------------------------------
class Clock:
    def wall(self): return time.time()
    def wall_ns(self): return time.time_ns()
    def mono_ns(self): return time.perf_counter_ns()
    def sleep(self, s): time.sleep(s)


class FireAborted(Exception):
    """The fire was aborted BEFORE any mutation was sent (a connection or probe failed, or a freshness gate failed)."""

    def __init__(self, reason, recs=None):
        super().__init__(reason)
        self.reason, self.recs = reason, recs or []


def wait_until(clock, target_s):
    while True:
        left = target_s - clock.wall()
        if left <= 0:
            return
        clock.sleep(min(left, 0.5) if left > 0.05 else 0)


def _new_rec(i, mode):
    d = FIXTURE["drafts"][i]
    return {"n": i + 1, "target_id": d["id"], "name": d["name"], "qty": d["qty"],
            "operation": "draftOrderComplete" if mode == "live" else "draftOrder(read)", "attempt_started": True,
            "connection_established": False, "probe_ok": False, "send_started": False, "send_completed": False,
            "http_response_received": False, "shopify_request_id": None, "mutation_response_received": False,
            "delivery_confirmed": False, "result": "NOT_SENT"}


def fire(tx, sess, mode, clock=None, fire_at_ms=None, marker=None):
    """Transport invariant: no mutation connection sits idle through a wait.
    1. All long waiting (--fire-at) happens first, with no mutation connection in existence.
    2. PRE_LEAD_S before the release, each worker thread opens its OWN connection and sends a read-only probe on that SAME connection.
    3. All workers meet at barrier `ready`. If any connection or probe failed, or any connection is older than MAX_CONN_AGE_S / idle
       longer than MAX_CONN_IDLE_S at the moment of release, the whole fire is aborted before any mutation (release barrier aborted).
    4. Otherwise the one-shot marker is written and the four mutations are released together from barrier `release`.
    No request is ever retried; a failed connection is never reused or reopened."""
    clock = clock or Clock()
    probe_bodies = [sess.body(PROBE, {"id": i}) for i in TARGET_IDS]
    action_bodies = [complete_body(i) for i in TARGET_IDS] if mode == "live" else probe_bodies
    hdr = sess.headers()
    if fire_at_ms is not None:
        wait_until(clock, fire_at_ms / 1000 - PRE_LEAD_S)
    ready, release = threading.Barrier(N + 1), threading.Barrier(N + 1)
    recs = [_new_rec(i, mode) for i in range(N)]
    conns = [None] * N

    def worker(i):
        rec = recs[i]
        try:
            rec["t_conn_start_ns"] = clock.mono_ns()
            conns[i] = tx.open()
            rec["t_connected_ns"] = clock.mono_ns()
            if not conns[i].is_open():
                raise RuntimeError("connection not open")
            rec["connection_established"] = True
            rec["t_probe_start_ns"] = clock.mono_ns()
            st, h, raw = conns[i].request("POST", GRAPHQL_PATH, probe_bodies[i], hdr)
            rec["t_probe_done_ns"] = clock.mono_ns()
            rec["probe_http_status"], rec["probe_request_id"] = st, h.get("x-request-id")
            d = json.loads(raw)
            dr = (d.get("data") or {}).get("draftOrder") or {}
            rec["probe_ok"] = bool(st == 200 and rec["probe_request_id"] and not d.get("errors") and dr.get("id") == TARGET_IDS[i] and dr.get("status") == "OPEN")
        except Exception as e:
            rec["probe_ok"], rec["probe_error"] = False, type(e).__name__
        try:
            ready.wait(timeout=READY_TIMEOUT_S)
            release.wait(timeout=READY_TIMEOUT_S)
        except threading.BrokenBarrierError:
            return                                                # aborted before the release: nothing was sent
        c = conns[i]
        rec["t_release_ns"], rec["release_utc"] = clock.mono_ns(), iso(clock.wall_ns())
        try:
            rec["t_send_ns"], rec["send_started"] = clock.mono_ns(), True
            recv = c.send_phase("POST", GRAPHQL_PATH, action_bodies[i], hdr)
            rec["t_sent_ns"], rec["send_completed"] = clock.mono_ns(), True
            resp = recv()
            rec["t_headers_ns"], rec["http_response_received"] = clock.mono_ns(), True
            rec["http_status"] = resp.status
            rid = resp.getheader("x-request-id")
            rec["shopify_request_id"] = rec["request_id"] = rid or None
            rec["delivery_confirmed"] = bool(rid)                 # a Shopify request id is the only proof Shopify received the request
            raw = resp.read()
            rec["t_done_ns"] = clock.mono_ns()
        except Exception as e:                                    # includes SSLEOF/timeouts: never delivered-by-assumption, never retried
            rec["t_done_ns"] = clock.mono_ns()
            rec["result"], rec["error"] = "UNKNOWN", type(e).__name__
            return
        try:
            d = json.loads(raw)
        except Exception:
            d = {}
            rec["error"] = "non-JSON response"
        rec["throttle"] = ((d.get("extensions") or {}).get("cost") or {}).get("throttleStatus")
        rec["graphql_errors"] = [e.get("message") for e in d.get("errors") or []]
        data = d.get("data") or {}
        if mode == "live":
            p = data.get("draftOrderComplete") or {}
            rec["mutation_response_received"] = "draftOrderComplete" in data
            rec["user_errors"], dr, ok_status = p.get("userErrors") or [], p.get("draftOrder") or {}, "COMPLETED"
        else:
            dr, ok_status = data.get("draftOrder") or {}, "OPEN"
            rec["mutation_response_received"] = "draftOrder" in data
            rec["user_errors"] = []
        rec["draft_status_after"] = dr.get("status")
        good = (resp.status == 200 and rec["delivery_confirmed"] and not rec["graphql_errors"] and not rec["user_errors"]
                and dr.get("id") == TARGET_IDS[i] and dr.get("status") == ok_status)
        rec["result"] = ("COMPLETED" if mode == "live" else "READ_OK") if good else "ERROR"

    threads = [threading.Thread(target=worker, args=(i,), name=f"fire-{i + 1}") for i in range(N)]
    [t.start() for t in threads]
    try:
        try:
            ready.wait(timeout=READY_TIMEOUT_S)
        except threading.BrokenBarrierError:
            raise FireAborted("workers were not all ready (open + probe)")
        bad = [f"{r['name']}: {r.get('probe_error') or 'probe not ok (HTTP %s)' % r.get('probe_http_status')}" for r in recs if not r["probe_ok"]]
        if bad:
            raise FireAborted("pre-release connection/probe failed: " + "; ".join(bad))
        if fire_at_ms is not None:
            wait_until(clock, fire_at_ms / 1000)
            if clock.wall() > fire_at_ms / 1000 + MAX_SLIP_S:
                raise FireAborted(f"release would slip more than {MAX_SLIP_S}s past --fire-at")
        now_ns = clock.mono_ns()
        stale = [f"{r['name']}: age {(now_ns - r['t_connected_ns']) / 1e9:.2f}s idle {(now_ns - r['t_probe_done_ns']) / 1e9:.2f}s" for r in recs
                 if (now_ns - r["t_connected_ns"]) / 1e9 > MAX_CONN_AGE_S or (now_ns - r["t_probe_done_ns"]) / 1e9 > MAX_CONN_IDLE_S]
        if stale:
            raise FireAborted(f"connection(s) too old at release (max age {MAX_CONN_AGE_S}s, max idle {MAX_CONN_IDLE_S}s): " + "; ".join(stale))
        if marker:                                                # written just before the release: a second live run is refused
            try:
                fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise FireAborted("one-shot marker already exists")
            os.write(fd, iso(clock.wall_ns()).encode())
            os.close(fd)
        t_main = clock.mono_ns()
        release.wait(timeout=READY_TIMEOUT_S)
    except BaseException as e:
        release.abort()
        ready.abort()
        [t.join() for t in threads]
        for c in conns:
            if c:
                c.close()
        if isinstance(e, FireAborted):
            e.recs = recs
        raise
    [t.join() for t in threads]
    for c in conns:
        if c:
            c.close()
    return recs, t_main


def _peak(iv):
    ev = sorted([(s, 1) for s, _ in iv] + [(e, -1) for _, e in iv], key=lambda x: (x[0], x[1]))
    cur = peak = 0
    for _, dl in ev:
        cur += dl
        peak = max(peak, cur)
    return peak


def summarise(recs):
    """Evidence semantics. Attempts are NOT deliveries and NOT concurrency. Only a Shopify request id proves delivery."""
    f = lambda k: sum(1 for r in recs if r.get(k))
    ids = [r["shopify_request_id"] for r in recs if r.get("shopify_request_id")]
    att = [(r["t_send_ns"], r["t_done_ns"]) for r in recs if r.get("send_started") and "t_done_ns" in r]
    conf = [(r["t_send_ns"], r["t_done_ns"]) for r in recs if r.get("delivery_confirmed") and "t_send_ns" in r and "t_done_ns" in r]
    peak_conf = _peak(conf)
    all_conf = len(conf) == N and max(s for s, _ in conf) < min(e for _, e in conf)
    return {"attempts_started": f("attempt_started"), "connections_established": f("connection_established"), "probes_ok": f("probe_ok"),
            "send_started": f("send_started"), "send_completed": f("send_completed"), "http_responses_received": f("http_response_received"),
            "shopify_request_ids_received": len(ids), "distinct_shopify_request_ids": len(set(ids)),
            "mutation_responses_received": f("mutation_response_received"), "delivery_confirmed": f("delivery_confirmed"),
            "client_overlap_attempts_DIAGNOSTIC_ONLY": {"peak_in_flight": _peak(att)},
            "client_overlap_confirmed": {"peak_in_flight": peak_conf, "all_four_overlap": all_conf},
            "shopify_concurrency_evidence": ("REQUEST_IDS_FOR_ALL_FOUR_AND_CLIENT_OVERLAP (necessary, not sufficient: server-side proof needs overlap.py cross-order overlap and four orders)"
                                             if all_conf and len(set(ids)) == N else "NONE"),
            "concurrency_verdict": "NOT_PROVEN" if not (all_conf and len(set(ids)) == N) else "CLIENT_SIDE_DELIVERY_OVERLAP_ONLY"}


def evaluate_evidence(ev):
    """Re-evaluate an evidence record under the corrected semantics. Works on the failed 30 Sep record (old schema, no evidence_summary)."""
    if "evidence_summary" in ev:
        return ev["evidence_summary"]
    recs = []
    for r in ev.get("requests") or []:
        rid = r.get("request_id")
        o = {"attempt_started": True, "send_started": "t_send_ms_after_main_release" in r, "send_completed": "t_sent_ms_after_main_release" in r,
             "http_response_received": r.get("http_status") is not None, "shopify_request_id": rid or None,
             "mutation_response_received": r.get("draft_status_after") is not None or bool(r.get("user_errors")),
             "delivery_confirmed": r.get("http_status") is not None and bool(rid), "connection_established": True, "probe_ok": False}
        if o["send_started"] and "t_done_ms_after_main_release" in r:
            o["t_send_ns"], o["t_done_ns"] = int(r["t_send_ms_after_main_release"] * 1e6), int(r["t_done_ms_after_main_release"] * 1e6)
        recs.append(o)
    s = summarise(recs)
    s["connections_established"] = s["probes_ok"] = None          # not recorded by the old schema
    return s


def render(recs, t_main):
    out = []
    for r in recs:
        o = dict(r)
        for k in ("t_conn_start_ns", "t_connected_ns", "t_probe_start_ns", "t_probe_done_ns", "t_release_ns", "t_send_ns", "t_sent_ns", "t_headers_ns", "t_done_ns"):
            if k in o:
                o[k.replace("_ns", "_ms_after_main_release")] = round((o.pop(k) - t_main) / 1e6, 3)
        if "t_release_ms_after_main_release" in o and "t_connected_ms_after_main_release" in o:
            o["connection_age_ms_at_release"] = round(o["t_release_ms_after_main_release"] - o["t_connected_ms_after_main_release"], 3)
            o["connection_idle_ms_at_release"] = round(o["t_release_ms_after_main_release"] - o["t_probe_done_ms_after_main_release"], 3)
        if "t_send_ms_after_main_release" in o and "t_done_ms_after_main_release" in o:
            o["duration_ms"] = round(o["t_done_ms_after_main_release"] - o["t_send_ms_after_main_release"], 3)
        out.append(o)
    return out


# ---- entry point --------------------------------------------------------------------------------------------------------------
def main(argv=None, env=None, tx=None, cf=None, out=None, clock=None):
    env = os.environ if env is None else env
    out = out or sys.stdout
    clock = clock or Clock()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["validate", "dry", "live", "evaluate"])
    ap.add_argument("--out")
    ap.add_argument("--evidence", help="evaluate mode: an evidence JSON file to re-evaluate offline")
    ap.add_argument("--state-dir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--fire-at", type=int, help="epoch milliseconds at which the barrier is released")
    ap.add_argument("--confirm", default="")
    a = ap.parse_args(argv)
    P = lambda *s: print(*s, file=out)
    if a.mode == "evaluate":                                      # offline: no network, no credentials
        try:
            ev = json.load(open(a.evidence))
        except Exception as e:
            P(f"REFUSED: cannot read evidence ({type(e).__name__})")
            return 2
        P(json.dumps(evaluate_evidence(ev), indent=1, sort_keys=True))
        return 0
    tx = tx or ShopifyTransport()
    cf = cf or CloudflareTransport()
    sess = None
    gates = []
    marker = os.path.join(a.state_dir, f"fire-{FIXTURE['competition_id']}.done") if a.mode == "live" else None
    try:
        if a.mode == "live":
            if a.confirm != CONFIRM_PHRASE:
                raise Refused("live mode needs --confirm with the exact S4 phrase (explicit S4 approval)")
            if FIXTURE["competition_id"] in RETIRED_COMPETITIONS:
                raise Refused(f"fixture {FIXTURE['competition_id']} is retired: {RETIRED_COMPETITIONS[FIXTURE['competition_id']]}")
            if os.path.exists(marker):
                raise Refused("a live fire was already attempted for this fixture (fire-*.done exists); no automatic re-run")
        if a.fire_at is not None and not (-2000 <= a.fire_at - clock.wall() * 1000 <= MAX_FIRE_AHEAD_MS):
            raise Refused("--fire-at must be within [-2 s, +30 min] of now")
        fails = check_config(a.state_dir)
        if fails:
            raise Refused("; ".join(fails))
        sess = Session(tx, env)
        fails = check_shopify(sess)
        gates = check_live_gates(cf, env, a.state_dir, clock.wall()) if (a.mode == "live" or env.get("CLOUDFLARE_API_TOKEN")) else ["not evaluated (no Cloudflare credentials)"]
        if fails:
            raise Refused("; ".join(fails))
        P(f"preconditions: PASS (shop {SHOP}, app {APP_NAME}, 4 drafts OPEN qty 1,1,2,2)")
        if a.mode == "live" and gates:
            raise Refused("live gates: " + "; ".join(gates))
        if a.mode == "validate":
            P("live gates now (informational): " + ("PASS" if not gates else "; ".join(gates)))
            P("VALIDATE OK: no mutation built or sent")
            return 0
        aborted, recs, t_main = None, None, None
        try:
            recs, t_main = fire(tx, sess, a.mode, clock, a.fire_at, marker)
        except FireAborted as e:
            aborted, recs, t_main = e.reason, e.recs, clock.mono_ns()
    except Refused as e:
        P(f"REFUSED (nothing sent): {e}")
        return 2
    summary = summarise(recs)
    ev = {"script_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(), "mode": a.mode, "shop": SHOP,
          "endpoint": f"https://{SHOP}{GRAPHQL_PATH}", "app": APP_NAME, "document": MUTATION if a.mode == "live" else PROBE,
          "fixture": {k: FIXTURE[k] for k in ("competition_id", "prefix", "product_gid", "variant_gid")},
          "transport_limits": {"pre_lead_s": PRE_LEAD_S, "max_conn_age_s": MAX_CONN_AGE_S, "max_conn_idle_s": MAX_CONN_IDLE_S, "max_slip_s": MAX_SLIP_S},
          "live_gates_at_start": gates, "aborted_before_release": aborted, "requests": render(recs, t_main), "evidence_summary": summary}
    blob = json.dumps(ev, indent=1, sort_keys=True)
    if any(s and s in blob for s in sess.secrets):
        raise SystemExit("BUG: a credential appeared in the evidence; not written")
    if a.out:
        with open(a.out, "w") as f:
            f.write(blob)
    if aborted:
        P(f"ABORTED BEFORE ANY MUTATION (nothing delivered): {aborted}")
        P("summary: " + json.dumps(summary))
        return 2
    P("n | target | qty | conn age ms | send ms | done ms | dur ms | HTTP | request id | delivered | result")
    for r in ev["requests"]:
        P(f"{r['n']} | {r['name']} {r['target_id'].split('/')[-1]} | {r['qty']} | {r.get('connection_age_ms_at_release')} | {r.get('t_send_ms_after_main_release')} | "
          f"{r.get('t_done_ms_after_main_release')} | {r.get('duration_ms')} | {r.get('http_status')} | {r.get('request_id')} | {r['delivery_confirmed']} | {r['result']}"
          + (f" errors={r.get('graphql_errors')} user_errors={r.get('user_errors')} exc={r.get('error')}" if r["result"] not in ("COMPLETED", "READ_OK") else ""))
    P("evidence: " + json.dumps(summary))
    ok_word = "COMPLETED" if a.mode == "live" else "READ_OK"
    if all(r["result"] == ok_word and r["delivery_confirmed"] for r in ev["requests"]):
        P(f"FIRE {a.mode.upper()} OK: 4/4 {ok_word}, 4/4 deliveries confirmed (Shopify request ids). Concurrency verdict: {summary['concurrency_verdict']}")
        return 0
    P(f"FIRE INCOMPLETE: deliveries confirmed {summary['delivery_confirmed']}/4. Concurrency NOT PROVEN. DO NOT RE-RUN. Inspect Shopify and D1 before any decision.")
    return 3


if __name__ == "__main__":
    sys.exit(main())
