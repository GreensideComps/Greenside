#!/usr/bin/env python3
"""gs: Greenside operating CLI, v1. READ-ONLY by construction: no Shopify mutation, no D1 write, no Cloudflare change, no deploy.

  gs.py status [--json]                 structured live state: repo, credential presence, Cloudflare, D1, Shopify apps, webhooks
  gs.py verify [--json]                 invariants from policy.json against live state; exit 0 all PASS, 1 any FAIL, 2 cannot judge
  gs.py audit claude-config [--ref REF] Claude permission files (working tree, ~/.claude, or a git ref) for risky pre-approvals
  gs.py audit credentials               credential NAMES present/absent (never values)
  gs.py d1 query "SELECT ..." [--json]  ad-hoc read-only SQL on the QA D1 (single SELECT only)
  gs.py test                            offline self-tests (gs, fire.py) [+ harness suites if installed]

Every invocation appends one line to the audit log (GS_AUDIT_LOG, default ~/.greenside/gs-audit.jsonl): time, command, exit code.
Outputs, arguments' values that look like secrets, and tokens are never logged. Reads credentials from the environment only.
Design and boundaries: docs/operating-layer.md. Transports are reused from qa/b3-stress-harness/b3stress/fire.py.
"""
import argparse, datetime, importlib.util, json, os, re, subprocess, sys, urllib.parse

sys.dont_write_bytecode = True        # loading fire.py by path must not leave __pycache__ in the harness tree

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
POLICY_PATH = os.path.join(HERE, "policy.json")
FIRE_PATH = os.path.join(REPO, "qa", "b3-stress-harness", "b3stress", "fire.py")
SNAP_SQL = os.path.join(REPO, "qa", "b3-stress-harness", "b3stress", "snap.sql")


# ---- read-only guards (safety-critical: mutation-tested in test_gs.py) --------------------------------------------------------
_DENY_SQL = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE", "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX",
             "TRUNCATE", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "UPSERT", "ANALYZE", "RETURNING", "EXPLAIN"}


def _strip_sql(sql):
    """Single left-to-right pass so comments and quoted literals are recognised in the order SQLite sees them (a '/*' inside a literal is
    not a comment; a quote inside a comment is not a literal). Returns the SQL with both replaced by a space, or None if unterminated."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j < 0:
                return None
            i = j + 2
            out.append(" ")
        elif c in "'\"`":
            j = i + 1
            while j < n:
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                return None
            out.append(" Q ")
            i = j + 1
        elif c == "[":
            j = sql.find("]", i)
            if j < 0:
                return None
            out.append(" Q ")
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def read_only_sql(sql):
    """(ok, reason). One SELECT (optionally WITH ... SELECT); no DML/DDL/PRAGMA/transaction keyword anywhere; no second statement."""
    if not isinstance(sql, str) or not sql.strip():
        return False, "empty SQL"
    s = _strip_sql(sql)
    if s is None:
        return False, "unterminated quote or bracket"
    s = s.strip()
    if s.endswith(";"):
        s = s[:-1].rstrip()
    if ";" in s:
        return False, "more than one statement"
    words = re.findall(r"[A-Za-z_]+", s)
    if not words or words[0].upper() not in ("SELECT", "WITH"):
        return False, "must start with SELECT or WITH"
    bad = sorted({w.upper() for w in words if w.upper() in _DENY_SQL})
    if bad:
        return False, f"forbidden keyword(s): {', '.join(bad)}"
    if words[0].upper() == "WITH" and "SELECT" not in {w.upper() for w in words}:
        return False, "WITH without SELECT"
    return True, "ok"


def read_only_graphql(doc):
    """(ok, reason). Only a query: no mutation or subscription operation anywhere."""
    if not isinstance(doc, str) or not doc.strip():
        return False, "empty document"
    s = re.sub(r"#[^\n]*", " ", doc)
    s = re.sub(r'"""(?:.|\n)*?"""|"(?:\\.|[^"\\])*"', ' "" ', s)
    ops = re.findall(r"\b(query|mutation|subscription)\b", s)
    if "mutation" in ops or "subscription" in ops:
        return False, "mutation/subscription not allowed"
    if not s.lstrip().startswith(("{", "query")):
        return False, "must start with { or query"
    return True, "ok"


# ---- clients ------------------------------------------------------------------------------------------------------------------
def _load_fire():
    spec = importlib.util.spec_from_file_location("fire", FIRE_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class CF:
    """Cloudflare: GET for state; the single D1 query POST only through d1_read(), which enforces read_only_sql."""

    def __init__(self, tx, env):
        self.tx, self.env = tx, env
        self.acct, self.tok = env.get("CLOUDFLARE_ACCOUNT_ID"), env.get("CLOUDFLARE_API_TOKEN")

    def ready(self):
        return bool(self.acct and self.tok)

    def _do(self, method, path, body=None):
        c = self.tx.open()
        try:
            h = {"Authorization": "Bearer " + self.tok}
            if body is not None:
                h["Content-Type"] = "application/json"
            st, _, raw = c.request(method, path, body, h)
        finally:
            c.close()
        try:
            return st, json.loads(raw)
        except Exception:
            return st, {}

    def get(self, path):
        return self._do("GET", f"/client/v4/accounts/{self.acct}/{path}")

    def d1_read(self, uuid, sql):
        ok, why = read_only_sql(sql)
        if not ok:
            raise ValueError("refused: " + why)
        if not re.fullmatch(r"[0-9a-f-]{36}", uuid or ""):
            raise ValueError("refused: bad database id")
        return self._do("POST", f"/client/v4/accounts/{self.acct}/d1/database/{uuid}/query", json.dumps({"sql": sql}))


class Shop:
    """Shopify Admin GraphQL, client-credentials, token in memory. Only read_only_graphql documents are sent."""

    def __init__(self, tx, policy, env, app):
        self.tx, self.p, self.app = tx, policy, app
        cfg = policy["apps"][app]
        cid, sec = env.get(cfg["env_id"]), env.get(cfg["env_secret"])
        self.token, self.scope = None, None
        if cid and sec:
            fire = self.tx.fire
            c = tx.open()
            try:
                st, _, raw = c.request("POST", fire.TOKEN_PATH, urllib.parse.urlencode(
                    {"client_id": cid, "client_secret": sec, "grant_type": "client_credentials"}), {"Content-Type": "application/x-www-form-urlencoded"})
            finally:
                c.close()
            try:
                j = json.loads(raw)
                self.token, self.scope = j.get("access_token"), j.get("scope")
            except Exception:
                pass

    def ready(self):
        return bool(self.token)

    def query(self, doc):
        ok, why = read_only_graphql(doc)
        if not ok:
            raise ValueError("refused: " + why)
        c = self.tx.open()
        try:
            st, _, raw = c.request("POST", f"/admin/api/{self.p['shopify_api_version']}/graphql.json", json.dumps({"query": doc}),
                                   {"Content-Type": "application/json", "X-Shopify-Access-Token": self.token})
        finally:
            c.close()
        try:
            return json.loads(raw)
        except Exception:
            return {"errors": [{"message": f"non-JSON response HTTP {st}"}]}


class Transports:
    """Real transports, reused from fire.py (fixed hosts, proxy tunnel). Tests replace this object with fakes."""

    def __init__(self):
        self.fire = _load_fire()
        self.shopify, self.cloudflare = self.fire.ShopifyTransport(), self.fire.CloudflareTransport()


class _Bound:
    def __init__(self, tx, fire):
        self._tx, self.fire = tx, fire

    def open(self):
        return self._tx.open()


# ---- state collection ---------------------------------------------------------------------------------------------------------
def collect(policy, env, tx, repo=REPO):
    st = {"time_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "errors": []}

    def guard(name, fn):
        try:
            st[name] = fn()
        except Exception as e:                                    # a failing section must not hide the others
            st[name] = None
            st["errors"].append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
    guard("repo", lambda: repo_state(repo))
    st["credentials"] = {n: (n in env and bool(env[n])) for n in policy["credential_env_names"]}
    st["unexpected_env_present"] = sorted(n for n in policy["unexpected_env_names"] if n in env)
    cf = CF(tx.cloudflare, env)
    if cf.ready():
        guard("cloudflare", lambda: cloudflare_state(cf, policy))
        guard("d1", lambda: d1_state(cf, policy))
    else:
        st["cloudflare"], st["d1"] = None, None
        st["errors"].append("cloudflare: credentials absent")
    st["shopify"] = {}
    for app in policy["apps"]:
        def one(app=app):
            s = Shop(_Bound(tx.shopify, tx.fire), policy, env, app)
            if not s.ready():
                return {"available": False}
            d = s.query("{ currentAppInstallation { app { title } accessScopes { handle } } webhookSubscriptions(first: 50) "
                        "{ nodes { topic endpoint { __typename ... on WebhookHttpEndpoint { callbackUrl } } } } shop { myshopifyDomain } }")
            a = (d.get("data") or {}).get("currentAppInstallation") or {}
            wh = (d.get("data") or {}).get("webhookSubscriptions") or {}
            return {"available": True, "token_scope": s.scope, "app": (a.get("app") or {}).get("title"), "shop": ((d.get("data") or {}).get("shop") or {}).get("myshopifyDomain"),
                    "scopes": sorted(x["handle"] for x in a.get("accessScopes") or []),
                    "webhooks": sorted((n["topic"], (n.get("endpoint") or {}).get("callbackUrl", "")) for n in wh.get("nodes") or []),
                    "errors": [e.get("message", "")[:100] for e in d.get("errors") or []]}
        guard_key = "shopify_" + app
        try:
            st["shopify"][app] = one()
        except Exception as e:
            st["shopify"][app] = None
            st["errors"].append(f"{guard_key}: {type(e).__name__}: {str(e)[:120]}")
    return st


def repo_state(repo):
    def g(*a):
        return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True).stdout.strip()
    return {"branch": g("rev-parse", "--abbrev-ref", "HEAD"), "head": g("rev-parse", "--short", "HEAD"),
            "dirty_files": [l[3:] for l in g("status", "--short").splitlines()]}


def cloudflare_state(cf, policy):
    c = policy["cloudflare"]
    st, d = cf.get("workers/scripts")
    names = sorted(x.get("id") for x in d.get("result") or [])
    pst, pd = cf.get(f"workers/scripts/{c['prod_worker']}/settings")
    prod_absent = pst == 404 or (pst == 200 and pd.get("success") is False)
    _, dep = cf.get(f"workers/scripts/{c['qa_worker']}/deployments")
    versions = (((dep.get("result") or {}).get("deployments") or [{}])[0]).get("versions") or []
    _, se = cf.get(f"workers/scripts/{c['qa_worker']}/settings")
    dry = [b.get("text") for b in (se.get("result") or {}).get("bindings", []) if b.get("name") == "DRY_RUN"]
    _, sc = cf.get(f"workers/scripts/{c['qa_worker']}/schedules")
    return {"workers": names, "prod_worker_absent": prod_absent, "qa_versions": [(v.get("version_id"), v.get("percentage")) for v in versions],
            "qa_dry_run": dry, "qa_crons": sorted(x.get("cron") for x in (sc.get("result") or {}).get("schedules", []))}


def d1_state(cf, policy):
    name = policy["cloudflare"]["qa_d1_name"]
    _, d = cf.get("d1/database?name=" + urllib.parse.quote(name))
    dbs = [x for x in d.get("result") or [] if x.get("name") == name]
    if len(dbs) != 1:
        return {"found": False}
    sql = open(SNAP_SQL).read()
    st, r = cf.d1_read(dbs[0]["uuid"], sql)
    row = ((((r.get("result") or [{}])[0]).get("results")) or [None])[0]
    if not row or row.get("marker") != "SW4C_SNAP":
        return {"found": True, "snapshot": False, "http": st}
    P = lambda k: row[k] if not isinstance(row[k], str) else json.loads(row[k])
    comps = P("competitions")
    ent = P("entries")
    per = {}
    for cid, key in policy["d1"].get("summary_pool_competitions", {}).items():   # pools that snap.sql reports as a summary, not as rows
        sm = P(key)
        per[cid] = {"AVAILABLE": sm["available"] or 0, "OTHER": sm["n"] - (sm["available"] or 0)}
    for e in ent:
        per.setdefault(e[0], {}).setdefault(e[3], 0)
        per[e[0]][e[3]] += 1
    return {"found": True, "snapshot": True, "integrity": P("integrity"),
            "competitions": [{"id": c["competition_id"], "prefix": c["prefix"], "status": c["status"], "capacity": c["capacity"], "pool": per.get(c["competition_id"], {})} for c in comps],
            "allocations": len(P("allocations")), "events": len(P("events")), "webhook_deliveries": len(P("webhook_deliveries"))}


# ---- invariants (pure functions of state + policy: mutation-tested) ---------------------------------------------------------
def checks(st, policy):
    R = []

    def add(i, ok, detail, skip=False, warn=False):
        R.append({"id": i, "status": "SKIP" if skip else ("PASS" if ok else ("WARN" if warn else "FAIL")), "detail": detail})
    cf, d1, sh = st.get("cloudflare"), st.get("d1"), st.get("shopify") or {}
    c = policy["cloudflare"]
    if cf is None:
        for i in ("cf.production_worker_absent", "cf.only_qa_worker", "cf.qa_single_version", "cf.qa_dry_run_true", "cf.qa_crons"):
            add(i, False, "Cloudflare state unavailable", skip=True)
    else:
        add("cf.production_worker_absent", cf["prod_worker_absent"] and c["prod_worker"] not in cf["workers"], f"workers={cf['workers']}")
        add("cf.only_qa_worker", cf["workers"] == [c["qa_worker"]], f"workers={cf['workers']}")
        add("cf.qa_single_version", len(cf["qa_versions"]) == 1 and cf["qa_versions"][0][1] == 100, f"versions={cf['qa_versions']}")
        add("cf.qa_dry_run_true", cf["qa_dry_run"] == [c["expected_dry_run"]], f"DRY_RUN={cf['qa_dry_run']} (a live window shows 'false' here)")
        add("cf.qa_crons", cf["qa_crons"] == sorted(c["expected_crons"]), f"crons={cf['qa_crons']}")
    if not d1 or not d1.get("snapshot"):
        add("d1.integrity_zero", False, "D1 snapshot unavailable", skip=True)
        add("d1.pools_consistent", False, "D1 snapshot unavailable", skip=True)
    else:
        bad = {k: d1["integrity"].get(k) for k in policy["d1"]["integrity_zero"] if d1["integrity"].get(k) != 0}
        add("d1.integrity_zero", not bad, f"nonzero={bad}" if bad else "all integrity counters 0")
        odd = [x["id"] for x in d1["competitions"] if x["status"] in ("OPEN", "DRAFT") and sum(x["pool"].values()) != x["capacity"]]
        add("d1.pools_consistent", not odd, f"pool size != capacity for {odd}" if odd else f"{len(d1['competitions'])} competitions, pool == capacity")
    for app, cfg in policy["apps"].items():
        s = sh.get(app)
        if not s or not s.get("available"):
            add(f"shopify.{app}.scopes", False, "credentials absent or token exchange failed", skip=True)
            continue
        sc = set(s["scopes"])
        if "scopes_exact" in cfg:
            ok = sc == set(cfg["scopes_exact"]) and not (sc & set(cfg["scopes_forbidden"]))
        else:
            ok = set(cfg["scopes_required"]) <= sc <= set(cfg["scopes_allowed"])
        add(f"shopify.{app}.scopes", ok, f"scopes={sorted(sc)}")
        add(f"shopify.{app}.identity", s["app"] == cfg["title"] and s["shop"] == policy["shop"], f"app={s['app']!r} shop={s['shop']!r}")
    wa = policy["webhooks"]["app"]
    s = sh.get(wa)
    if not s or not s.get("available"):
        add("shopify.webhooks", False, "allocator credentials unavailable", skip=True)
    else:
        topics = sorted(t for t, _ in s["webhooks"])
        urls_ok = all(u.startswith(policy["webhooks"]["callback_prefix"]) for _, u in s["webhooks"])
        add("shopify.webhooks", topics == sorted(policy["webhooks"]["topics"]) and urls_ok, f"topics={topics} all_to_qa_worker={urls_ok}")
    unexpected = st.get("unexpected_env_present") or []
    add("env.no_unexpected_credentials", not unexpected, f"present (names only): {unexpected}; container-level, origin unverified" if unexpected else "none", warn=True)
    missing = [n for n, ok in (st.get("credentials") or {}).items() if not ok]
    add("env.expected_credentials_present", not missing, f"missing: {missing}" if missing else "all present (names only)")
    return R


def verdict(results, errors=()):
    if any(r["status"] == "FAIL" for r in results):
        return 1
    if any(r["status"] == "SKIP" for r in results) or errors:
        return 2
    return 0


# ---- claude-config audit ---------------------------------------------------------------------------------------------------------
def audit_claude_config(policy, paths, ref=None, repo=REPO):
    risky, found = set(policy["claude_config"]["risky_allow_patterns"]), []
    sources = []
    for p in paths:
        if os.path.isfile(p):
            try:
                sources.append((p, json.load(open(p))))
            except Exception:
                sources.append((p, None))
    if ref:
        for f in (".claude/settings.json", ".claude/settings.local.json"):
            r = subprocess.run(["git", "-C", repo, "show", f"{ref}:{f}"], capture_output=True, text=True)
            if r.returncode == 0:
                try:
                    sources.append((f"{ref}:{f}", json.loads(r.stdout)))
                except Exception:
                    sources.append((f"{ref}:{f}", None))
    out = {"sources": [s for s, _ in sources], "findings": [], "deny_rules": {}}
    for name, d in sources:
        if d is None:
            out["findings"].append({"source": name, "issue": "unparseable settings file"})
            continue
        perms = d.get("permissions") or {}
        out["deny_rules"][name] = len(perms.get("deny") or [])
        for a in perms.get("allow") or []:
            if a in risky:
                out["findings"].append({"source": name, "issue": f"pre-approved write-capable tool: {a}"})
        if d.get("hooks"):
            out["hooks_defined_in"] = out.get("hooks_defined_in", []) + [name]
    if not any(out["deny_rules"].values()):
        out["findings"].append({"source": "*", "issue": "no deny rules in any inspected settings file (production-write tools are not blocked by configuration)"})
    return out


# ---- audit log + CLI ---------------------------------------------------------------------------------------------------------------
def audit_log(argv, rc, env):
    path = env.get("GS_AUDIT_LOG") or os.path.expanduser("~/.greenside/gs-audit.jsonl")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cmd = [a for a in argv[:2]]                               # command words only; free-text arguments (e.g. SQL) are logged as a length
        extra = {"sql_chars": len(argv[2])} if len(argv) > 2 and argv[0] == "d1" else {}
        with open(path, "a") as f:
            f.write(json.dumps({"t": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "cmd": cmd, "rc": rc, "mode": "read-only", **extra}) + "\n")
    except OSError:
        pass


def render_status(st):
    L = [f"Greenside state at {st['time_utc']} (UTC)"]
    r = st.get("repo") or {}
    L.append(f"repo: {r.get('branch')} @ {r.get('head')}, {len(r.get('dirty_files', []))} uncommitted file(s)")
    L.append("credentials (names): " + ", ".join(f"{k}={'present' if v else 'ABSENT'}" for k, v in st["credentials"].items()))
    if st.get("unexpected_env_present"):
        L.append("unexpected credential-like env names present: " + ", ".join(st["unexpected_env_present"]))
    cf = st.get("cloudflare")
    if cf:
        L.append(f"cloudflare: workers={cf['workers']} production_absent={cf['prod_worker_absent']} qa_versions={cf['qa_versions']} DRY_RUN={cf['qa_dry_run']} crons={cf['qa_crons']}")
    d1 = st.get("d1")
    if d1 and d1.get("snapshot"):
        L.append(f"d1 (QA): competitions={[(c['prefix'], c['status'], c['pool']) for c in d1['competitions']]} allocations={d1['allocations']} events={d1['events']} integrity_nonzero={ {k: v for k, v in d1['integrity'].items() if v and k not in ('events_total', 'allocations_total')} }")
    for app, s in st["shopify"].items():
        L.append(f"shopify {app}: " + (f"{s['app']} scopes={s['scopes']} webhooks={len(s['webhooks'])}" if s and s.get("available") else "unavailable"))
    for e in st["errors"]:
        L.append("ERROR " + e)
    return "\n".join(L)


def run_tests():
    rc = 0
    for f in (os.path.join(HERE, "test_gs.py"),):
        rc |= subprocess.run([sys.executable, f]).returncode
    fire_t = os.path.join(REPO, "qa", "b3-stress-harness", "b3stress-selftest-cmds", "firetest.py")
    if os.path.exists(fire_t):
        rc |= subprocess.run([sys.executable, fire_t, os.path.dirname(FIRE_PATH)]).returncode
    return 1 if rc else 0


def main(argv=None, env=None, tx=None, out=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    out = out or sys.stdout
    P = lambda *s: print(*s, file=out)
    policy = json.load(open(POLICY_PATH))
    ap = argparse.ArgumentParser(prog="gs", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("status", "verify"):
        p = sub.add_parser(n); p.add_argument("--json", action="store_true")
    a = sub.add_parser("audit"); a.add_argument("what", choices=["claude-config", "credentials"]); a.add_argument("--ref"); a.add_argument("--json", action="store_true")
    d = sub.add_parser("d1"); d.add_argument("op", choices=["query"]); d.add_argument("sql"); d.add_argument("--json", action="store_true")
    sub.add_parser("test")
    args = ap.parse_args(argv)
    rc = 0
    if args.cmd == "test":
        rc = run_tests()
    elif args.cmd in ("status", "verify"):
        st = collect(policy, env, tx or Transports())
        if args.cmd == "status":
            P(json.dumps(st, indent=1, sort_keys=True) if args.json else render_status(st))
        else:
            res = checks(st, policy)
            rc = verdict(res, st["errors"])
            if args.json:
                P(json.dumps({"results": res, "errors": st["errors"], "exit": rc}, indent=1))
            else:
                for r in res:
                    P(f"{r['status']:4} {r['id']:36} {r['detail']}")
                for e in st["errors"]:
                    P("ERROR " + e)
                P({0: "VERIFY: ALL PASS", 1: "VERIFY: FAIL", 2: "VERIFY: INCONCLUSIVE (some checks could not run)"}[rc])
    elif args.cmd == "audit":
        if args.what == "credentials":
            names = {n: (n in env and bool(env[n])) for n in policy["credential_env_names"] + policy["unexpected_env_names"]}
            P(json.dumps(names, indent=1) if args.json else "\n".join(f"{n}: {'present' if v else 'absent'}" for n, v in names.items()))
        else:
            r = audit_claude_config(policy, [os.path.join(REPO, ".claude", "settings.json"), os.path.join(REPO, ".claude", "settings.local.json"),
                                             os.path.expanduser("~/.claude/settings.json"), os.path.expanduser("~/.claude/settings.local.json")], args.ref)
            P(json.dumps(r, indent=1) if args.json else "\n".join(["sources: " + ", ".join(r["sources"] or ["(none found)"])] + ["FINDING " + f"{x['source']}: {x['issue']}" for x in r["findings"]] or ["no findings"]))
            rc = 1 if r["findings"] else 0
    elif args.cmd == "d1":
        ok, why = read_only_sql(args.sql)
        if not ok:
            P("REFUSED: " + why)
            rc = 2
        else:
            t = tx or Transports()
            cf = CF(t.cloudflare, env)
            if not cf.ready():
                P("REFUSED: Cloudflare credentials absent")
                rc = 2
            else:
                _, dd = cf.get("d1/database?name=" + urllib.parse.quote(policy["cloudflare"]["qa_d1_name"]))
                dbs = [x for x in dd.get("result") or [] if x.get("name") == policy["cloudflare"]["qa_d1_name"]]
                if len(dbs) != 1:
                    P("REFUSED: QA D1 not found")
                    rc = 2
                else:
                    st, r = cf.d1_read(dbs[0]["uuid"], args.sql)
                    rows = (((r.get("result") or [{}])[0]).get("results")) or []
                    P(json.dumps(rows, indent=1) if args.json else "\n".join(json.dumps(x) for x in rows))
                    rc = 0 if st == 200 and r.get("success") else 1
    audit_log(argv, rc, env)
    return rc


if __name__ == "__main__":
    sys.exit(main())
