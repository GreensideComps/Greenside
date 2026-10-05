#!/usr/bin/env python3
"""Offline, deterministic self-tests for tools/gs/gs.py. No network, no credentials. Fake Cloudflare and Shopify transports record every
request, so the tests assert what was and was NOT sent. Usage: test_gs.py [GS_DIR]  (default: this directory).
Prints PASS/FAIL lines and 'GS SELF-TEST: n/m passed'; exit 0 iff all pass. mutation_check.py runs this against deliberately broken copies."""
import sys
sys.dont_write_bytecode = True
import copy, importlib.util, io, json, os, sys, tempfile

D = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("gs", os.path.join(D, "gs.py"))
G = importlib.util.module_from_spec(spec); spec.loader.exec_module(G)
POLICY = json.load(open(os.path.join(D, "policy.json")))
RES = []
SECRETS = {"CLOUDFLARE_API_TOKEN": "CFTOK-SECRET-aa11", "SHOPIFY_CLIENT_SECRET": "ALLOC-SECRET-bb22", "QA_STRESS_SHOPIFY_CLIENT_SECRET": "STRESS-SECRET-cc33",
           "SHOPIFY_CLIENT_ID": "ALLOC-ID-dd44", "QA_STRESS_SHOPIFY_CLIENT_ID": "STRESS-ID-ee55", "CLOUDFLARE_ACCOUNT_ID": "acct123"}
TOK = {"ALLOC-ID-dd44": "TOKEN-ALLOC-ff66", "STRESS-ID-ee55": "TOKEN-STRESS-gg77"}


def chk(name, ok, detail=""):
    RES.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)[:300]))


# ---- fakes ---------------------------------------------------------------------------------------------------------------------
class World:
    def __init__(self):
        self.workers = ["greenside-entry-allocator-qa"]; self.prod_present = False
        self.versions = [{"version_id": "v1", "percentage": 100}]; self.dry = "true"; self.crons = ["*/15 * * * *", "20,50 * * * *"]
        self.integrity = {k: 0 for k in POLICY["d1"]["integrity_zero"]}; self.integrity.update(events_total=41, allocations_total=15)
        self.comps = [("15905304379766", "QAI", "OPEN", 6, 6), ("900001", "PUT", "OPEN", 100, 0)]      # id, prefix, status, capacity, pool rows (PUT is summarised)
        self.scopes = {"allocator": ["read_orders", "read_products"], "stress_driver": ["write_draft_orders", "read_draft_orders"]}
        self.titles = {"allocator": "Greenside Entry Allocator", "stress_driver": "Greenside QA Stress Driver"}
        self.shop = POLICY["shop"]; self.hooks = [(t, POLICY["webhooks"]["callback_prefix"] + "/webhook") for t in POLICY["webhooks"]["topics"]]
        self.log = []


class Conn:
    def __init__(self, w, kind): self.w, self.kind = w, kind
    def close(self): pass

    def request(self, method, path, body, headers):
        w = self.w; w.log.append({"kind": self.kind, "method": method, "path": path, "body": body, "headers": dict(headers or {})})
        if self.kind == "cf":
            assert headers["Authorization"] == "Bearer " + SECRETS["CLOUDFLARE_API_TOKEN"]
            j = lambda o, st=200: (st, {}, json.dumps(o).encode())
            if path.endswith("/workers/scripts"): return j({"result": [{"id": n} for n in w.workers]})
            if path.endswith("/greenside-entry-allocator/settings"): return j({"success": True, "result": {"bindings": []}}) if w.prod_present else j({"success": False}, 404)
            if path.endswith("/deployments"): return j({"result": {"deployments": [{"versions": w.versions}]}})
            if path.endswith("/greenside-entry-allocator-qa/settings"): return j({"result": {"bindings": [{"name": "DRY_RUN", "text": w.dry}]}})
            if path.endswith("/schedules"): return j({"result": {"schedules": [{"cron": c} for c in w.crons]}})
            if "/d1/database?name=" in path: return j({"result": [{"uuid": "5c7ecce9-5b2f-45eb-b5a6-ab78f14698be", "name": POLICY["cloudflare"]["qa_d1_name"]}]})
            if path.endswith("/query"):
                sql = json.loads(body)["sql"]
                if "SW4C_SNAP" in sql:
                    ent = [[c[0], 1000 + i, f"{c[1]}{1000 + i}", "AVAILABLE", None, None, None, None, 0, None, None, None] for c in w.comps for i in range(c[4])]
                    comps = [{"competition_id": c[0], "prefix": c[1], "status": c[2], "capacity": c[3]} for c in w.comps]
                    row = {"marker": "SW4C_SNAP", "competitions": json.dumps(comps), "entries": json.dumps(ent), "allocations": "[]", "events": "[]", "webhook_deliveries": "[]", "integrity": json.dumps(w.integrity), "put_summary": json.dumps({"n": 100, "available": 100})}
                    return j({"success": True, "result": [{"results": [row]}]})
                return j({"success": True, "result": [{"results": [{"n": 1}]}]})
            raise AssertionError("unexpected CF path " + path)
        if path == "/admin/oauth/access_token":
            cid = dict(x.split("=") for x in body.split("&"))["client_id"]
            return 200, {}, json.dumps({"access_token": TOK[cid], "scope": "read_orders,read_products" if cid == "ALLOC-ID-dd44" else "write_draft_orders"}).encode()
        app = "allocator" if headers["X-Shopify-Access-Token"] == TOK["ALLOC-ID-dd44"] else "stress_driver"
        d = {"currentAppInstallation": {"app": {"title": w.titles[app]}, "accessScopes": [{"handle": h} for h in w.scopes[app]]}, "shop": {"myshopifyDomain": w.shop},
             "webhookSubscriptions": {"nodes": [{"topic": t, "endpoint": {"__typename": "WebhookHttpEndpoint", "callbackUrl": u}} for t, u in (w.hooks if app == "allocator" else [])]}}
        return 200, {}, json.dumps({"data": d}).encode()


class Tx:
    def __init__(self, w): self.w = w; self.fire = G._load_fire(); self.cloudflare = Side(w, "cf"); self.shopify = Side(w, "shopify")


class Side:
    def __init__(self, w, k): self.w, self.k = w, k
    def open(self): return Conn(self.w, self.k)


def state(w=None, env=None):
    w = w or World(); env = SECRETS if env is None else env
    return G.collect(POLICY, env, Tx(w), repo=os.path.dirname(os.path.dirname(D))), w


def verify(mut=None, env=None):
    w = World()
    if mut: mut(w)
    st, w = state(w, env)
    return {r["id"]: r for r in G.checks(st, POLICY)}, G.verdict(G.checks(st, POLICY), st["errors"]), w, st


# ---- 1. SQL guard ----------------------------------------------------------------------------------------------------------------
ok = lambda s: G.read_only_sql(s)[0]
chk("sql: the harness snapshot query (snap.sql) is accepted", ok(open(os.path.join(G.SNAP_SQL)).read()))
chk("sql: plain SELECT, WITH..SELECT and a trailing semicolon are accepted", ok("SELECT 1") and ok("select * from competition;") and ok("WITH a AS (SELECT 1) SELECT * FROM a"))
chk("sql: column names containing keywords (released_at, release_reason, replaced_by) are accepted", ok("SELECT released_at, release_reason FROM entry_number"))
chk("sql: keywords inside string literals are accepted", ok("SELECT * FROM t WHERE x = 'DROP TABLE; DELETE'") and ok("SELECT 'it''s; DROP'"))
for bad, why in [("INSERT INTO t VALUES (1)", "insert"), ("UPDATE t SET a=1", "update"), ("DELETE FROM t", "delete"), ("DROP TABLE t", "drop"), ("ALTER TABLE t ADD c", "alter"),
                 ("CREATE TABLE t(a)", "create"), ("REPLACE INTO t VALUES(1)", "replace"), ("PRAGMA writable_schema=1", "pragma"), ("ATTACH DATABASE 'x' AS y", "attach"),
                 ("VACUUM", "vacuum"), ("BEGIN", "begin"), ("SELECT 1; DELETE FROM t", "second statement"), ("SELECT 1;DROP TABLE t", "second statement no space"),
                 ("SELECT 1 /* x */; DROP TABLE t", "comment then statement"), ("SELECT 1 -- x\n; DROP TABLE t", "line comment then statement"),
                 ("sElEcT 1; dRoP table t", "mixed case"), ("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", "DML in CTE"),
                 ("SELECT 'abc", "unterminated literal"), ("", "empty"), ("   ", "blank"), ("EXPLAIN SELECT 1", "explain"), ("SELECT 1 UNION SELECT 2; PRAGMA x", "pragma after union"),
                 ("/* SELECT */ DELETE FROM t", "leading comment then delete")]:
    chk(f"sql: refuses {why}", not ok(bad), bad)
chk("sql: refuses non-string input", not ok(None) and not ok(123))
# layer-isolating cases: each is refused ONLY by one specific layer of the guard (so removing that layer must fail a test)
chk("sql layer keyword-deny: DELETE inside a WITH statement (no RETURNING, starts with WITH, contains SELECT) is refused", not ok("WITH x AS (SELECT 1) DELETE FROM t"))
chk("sql layer keyword-deny: PRAGMA after a SELECT is refused", not ok("SELECT 1 PRAGMA x"))
chk("sql layer keyword-deny: DROP / INSERT / UPDATE after a SELECT are refused", not ok("SELECT 1 DROP") and not ok("SELECT 1 INSERT") and not ok("SELECT 1 UPDATE"))
chk("sql layer one-statement: two SELECT statements are refused", not ok("SELECT 1; SELECT 2"))
chk("sql layer first-keyword: unknown leading verbs are refused (FOOBAR, SET, CALL, MERGE, a lone WITH)", not ok("FOOBAR 1") and not ok("SET x = 1") and not ok("CALL evil()") and not ok("MERGE INTO t") and not ok("WITH x AS (1)"))
chk("sql layer comments: a comment containing ';' and DELETE is harmless and accepted", ok("SELECT 1 /* a; DELETE FROM t */") and ok("SELECT 1 -- x; DROP TABLE t"))
chk("sql layer comments: a quote inside a comment cannot hide a second statement (block comment)", not ok("SELECT 1 /* ' */ ; DELETE FROM t -- '"))
chk("sql layer comments: a quote inside a line comment cannot hide a second statement", not ok("SELECT 1 -- '\n; DELETE FROM t -- '"))
chk("sql layer literals: a literal containing '--' or '/*' does not swallow a following statement", not ok("SELECT '--' ; DELETE FROM t") and not ok("SELECT '/*' ; DELETE FROM t; SELECT '*/'"))

# ---- 2. GraphQL guard --------------------------------------------------------------------------------------------------------------
gq = lambda s: G.read_only_graphql(s)[0]
chk("graphql: queries accepted (shorthand and named)", gq("{ shop { name } }") and gq("query Q { shop { name } }"))
chk("graphql: mutation and subscription refused, including after a query and in any case position", not gq("mutation { x }") and not gq("{ a } mutation M { b }") and not gq("query Q { a } subscription S { b }"))
chk("graphql: 'mutation' inside a string or comment is not an operation", gq('{ a(x: "mutation") }') and gq("{ a } # mutation { b }"))
chk("graphql: empty / non-string refused", not gq("") and not gq(None))

# ---- 3. clients refuse before any network ------------------------------------------------------------------------------------------
w = World(); cf = G.CF(Side(w, "cf"), SECRETS)
for sql in ("DELETE FROM competition", "SELECT 1; DROP TABLE x", "PRAGMA foreign_keys=off"):
    try: cf.d1_read("5c7ecce9-5b2f-45eb-b5a6-ab78f14698be", sql); r = False
    except ValueError: r = True
    chk(f"CF.d1_read refuses '{sql[:24]}' before any request", r and not w.log)
try: cf.d1_read("not-a-uuid", "SELECT 1"); r = False
except ValueError: r = True
chk("CF.d1_read refuses a malformed database id", r and not w.log)
sh = G.Shop(G._Bound(Side(w, "shopify"), G._load_fire()), POLICY, SECRETS, "allocator"); n0 = len(w.log)
try: sh.query("mutation { productDelete(input:{id:\"x\"}) { deletedProductId } }"); r = False
except ValueError: r = True
chk("Shop.query refuses a mutation before any request", r and len(w.log) == n0)

# ---- 4. healthy state, and the exact request surface ---------------------------------------------------------------------------------
res, rc, w, st = verify()
chk("verify: healthy fake state -> every check PASS, exit 0", rc == 0 and all(r["status"] == "PASS" for r in res.values()), {k: v["status"] for k, v in res.items() if v["status"] != "PASS"})
posts = [x for x in w.log if x["method"] == "POST"]
chk("surface: the only POSTs are token exchange, read-only GraphQL and read-only D1 SELECT", all(x["path"] == "/admin/oauth/access_token" or "/graphql.json" in x["path"] or x["path"].endswith("/query") for x in posts))
chk("surface: every GraphQL document sent is a query; every D1 statement sent passes the SQL guard",
    all(G.read_only_graphql(json.loads(x["body"])["query"])[0] for x in posts if "/graphql.json" in x["path"]) and all(G.read_only_sql(json.loads(x["body"])["sql"])[0] for x in posts if x["path"].endswith("/query")))
chk("surface: every Cloudflare request is a GET or the D1 query POST; no PUT/PATCH/DELETE anywhere", {x["method"] for x in w.log} <= {"GET", "POST"} and all(x["method"] == "GET" or x["path"].endswith("/query") for x in w.log if x["kind"] == "cf"))
chk("surface: state has no secret values", not any(s in json.dumps(st) for s in list(SECRETS.values()) + list(TOK.values())))

# ---- 5. every invariant fails when its condition is broken ----------------------------------------------------------------------------
def broken(name, mut, ids, env=None, want_rc=1):
    res, rc, w, st = verify(mut, env)
    chk(f"verify: {name} -> {', '.join(ids)} FAIL, exit {want_rc}", all(res[i]["status"] == "FAIL" for i in ids) and rc == want_rc, {i: res[i]["status"] for i in ids} | {"rc": rc})

broken("production Worker exists", lambda w: (setattr(w, "prod_present", True), w.workers.append("greenside-entry-allocator")), ["cf.production_worker_absent", "cf.only_qa_worker"])
broken("an extra Worker exists", lambda w: w.workers.append("mystery"), ["cf.only_qa_worker"])
broken("QA DRY_RUN is false", lambda w: setattr(w, "dry", "false"), ["cf.qa_dry_run_true"])
broken("QA DRY_RUN unset", lambda w: setattr(w, "dry", None), ["cf.qa_dry_run_true"])
broken("QA split across two versions", lambda w: setattr(w, "versions", [{"version_id": "a", "percentage": 50}, {"version_id": "b", "percentage": 50}]), ["cf.qa_single_version"])
broken("QA cron changed", lambda w: setattr(w, "crons", ["*/5 * * * *"]), ["cf.qa_crons"])
broken("D1 integrity counter non-zero", lambda w: w.integrity.update(ledger_drift=1), ["d1.integrity_zero"])
broken("D1 pool size differs from capacity", lambda w: setattr(w, "comps", [("1", "QAI", "OPEN", 6, 5)]), ["d1.pools_consistent"])
broken("the summarised PUT pool is short", lambda w: setattr(w, "comps", [("900001", "PUT", "OPEN", 101, 0)]), ["d1.pools_consistent"])
broken("allocator holds write_inventory", lambda w: w.scopes.update(allocator=["read_orders", "read_products", "write_inventory"]), ["shopify.allocator.scopes"])
broken("allocator lost a scope", lambda w: w.scopes.update(allocator=["read_orders"]), ["shopify.allocator.scopes"])
broken("stress driver gained write_products", lambda w: w.scopes.update(stress_driver=["write_draft_orders", "read_draft_orders", "write_products"]), ["shopify.stress_driver.scopes"])
broken("stress driver lost write_draft_orders", lambda w: w.scopes.update(stress_driver=["read_draft_orders"]), ["shopify.stress_driver.scopes"])
broken("allocator app has the wrong name", lambda w: w.titles.update(allocator="Other App"), ["shopify.allocator.identity"])
broken("shop domain differs", lambda w: setattr(w, "shop", "other.myshopify.com"), ["shopify.allocator.identity", "shopify.stress_driver.identity"])
broken("a webhook topic is missing", lambda w: w.hooks.pop(), ["shopify.webhooks"])
broken("a webhook points elsewhere", lambda w: w.hooks.__setitem__(0, (w.hooks[0][0], "https://evil.example/hook")), ["shopify.webhooks"])
res, rc, w, st = verify(None, env={**SECRETS, "AWS_ACCESS_KEY_ID": "x"})
chk("verify: an unexpected AWS credential name -> env.no_unexpected_credentials WARN (names only), exit stays 0", res["env.no_unexpected_credentials"]["status"] == "WARN" and rc == 0 and "AWS_ACCESS_KEY_ID" in res["env.no_unexpected_credentials"]["detail"] and "x" not in res["env.no_unexpected_credentials"]["detail"].replace("AWS_ACCESS_KEY_ID", "").replace("container-level, origin unverified", "").replace("present (names only): ", "").replace("[", "").replace("]", "").replace("'", "").replace(";", "").replace(" ", "").replace("-", "").replace(",", ""))
broken("a required credential is missing", None, ["env.expected_credentials_present"], env={k: v for k, v in SECRETS.items() if k != "SHOPIFY_CLIENT_SECRET"}, want_rc=1)
res, rc, w, st = verify(None, env={})
chk("verify: with no credentials nothing is judged PASS by omission: exit 2 or 1, never 0", rc != 0 and all(res[i]["status"] in ("SKIP", "FAIL") for i in ("cf.qa_dry_run_true", "d1.integrity_zero", "shopify.webhooks")))

# verdict semantics, isolated (SKIP must never read as PASS; FAIL must dominate)
V = G.verdict
chk("verdict: all PASS -> 0; any FAIL -> 1 even with SKIPs; SKIP only -> 2; errors only -> 2",
    V([{"status": "PASS"}]) == 0 and V([{"status": "PASS"}, {"status": "FAIL"}, {"status": "SKIP"}]) == 1 and V([{"status": "PASS"}, {"status": "SKIP"}]) == 2 and V([{"status": "PASS"}], ["boom"]) == 2)

# ---- 6. claude-config audit --------------------------------------------------------------------------------------------------------------
tmp = tempfile.mkdtemp(prefix="gs-")
p1 = os.path.join(tmp, "s1.json"); json.dump({"permissions": {"allow": ["mcp__Shopify__graphql_mutation", "mcp__Shopify__get-product"]}}, open(p1, "w"))
r = G.audit_claude_config(POLICY, [p1])
chk("audit: pre-approved Shopify mutation tool is a finding", any("graphql_mutation" in f["issue"] for f in r["findings"]))
chk("audit: absence of deny rules is a finding", any("no deny rules" in f["issue"] for f in r["findings"]))
p2 = os.path.join(tmp, "s2.json"); json.dump({"permissions": {"allow": ["mcp__Shopify__get-product"], "deny": ["mcp__Shopify__graphql_mutation"]}}, open(p2, "w"))
r = G.audit_claude_config(POLICY, [p2])
chk("audit: a read-only allow list with a deny rule has no findings", r["findings"] == [], r["findings"])
p3 = os.path.join(tmp, "s3.json"); open(p3, "w").write("{not json")
chk("audit: an unparseable settings file is a finding, not a crash", any("unparseable" in f["issue"] for f in G.audit_claude_config(POLICY, [p3])["findings"]))

# ---- 7. CLI, audit log, secrets -------------------------------------------------------------------------------------------------------------
def cli(argv, w=None, env=None):
    w = w or World(); out = io.StringIO(); e = dict(SECRETS if env is None else env); e["GS_AUDIT_LOG"] = os.path.join(tmp, "audit.jsonl")
    rc = G.main(argv, env=e, tx=Tx(w), out=out); return rc, out.getvalue(), w

if os.path.exists(os.path.join(tmp, "audit.jsonl")): os.remove(os.path.join(tmp, "audit.jsonl"))
rc, out, w = cli(["verify"])
chk("cli: gs verify prints ALL PASS and exits 0 on the healthy fake", rc == 0 and "VERIFY: ALL PASS" in out, out[-200:])
rc, out, w = cli(["status", "--json"])
chk("cli: gs status --json is valid JSON containing no secret or token", json.loads(out) and not any(s in out for s in list(SECRETS.values()) + list(TOK.values())))
rc, out, w = cli(["d1", "query", "DELETE FROM competition"])
chk("cli: gs d1 query with a write statement is REFUSED (exit 2) and nothing is sent", rc == 2 and "REFUSED" in out and not w.log)
rc, out, w = cli(["d1", "query", "SELECT 1 AS n"])
chk("cli: gs d1 query with a SELECT runs and returns rows", rc == 0 and '"n": 1' in out, out)
rc, out, w = cli(["d1", "query", "SELECT 1"], env={})
chk("cli: gs d1 query without Cloudflare credentials is refused", rc == 2 and "credentials absent" in out)
rc, out, w = cli(["audit", "credentials"])
chk("cli: gs audit credentials prints names and present/absent only", "CLOUDFLARE_API_TOKEN: present" in out and not any(s in out for s in SECRETS.values()))
log = open(os.path.join(tmp, "audit.jsonl")).read()
lines = [json.loads(l) for l in log.splitlines()]
chk("audit log: one JSON line per invocation with time, command, exit code and mode", len(lines) == 6 and all({"t", "cmd", "rc", "mode"} <= set(l) for l in lines) and all(l["mode"] == "read-only" for l in lines), lines)
chk("audit log: never records SQL text, secrets or tokens", "DELETE FROM competition" not in log and "SELECT 1 AS n" not in log and not any(s in log for s in list(SECRETS.values()) + list(TOK.values())))
chk("audit log: records the refusal exit code (2) for the refused statement", any(l["cmd"][:2] == ["d1", "query"] and l["rc"] == 2 and l.get("sql_chars") == len("DELETE FROM competition") for l in lines))

n, m = sum(RES), len(RES)
print(f"GS SELF-TEST: {n}/{m} passed")
sys.exit(0 if n == m else 1)
