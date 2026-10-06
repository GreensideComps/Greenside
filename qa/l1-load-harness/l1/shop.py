"""Shopify Admin GraphQL client for L1 tools. Every client is built with an explicit allow-list of documents; nothing else can be
sent. Read-only clients additionally require every document to pass tools/gs read_only_graphql (no mutation, no subscription).

Two apps only:
  stress_driver  Greenside QA Stress Driver (write_draft_orders): stage.py, canary.py, load.py
  allocator      Greenside Entry Allocator (read_orders, read_products): READ-ONLY; sampler.py, clamp.py, shopsnapl.py, prechecks
Credentials are environment variable NAMES only; the token lives in memory and is scrubbed from every evidence line.
Transport: fire.py's fixed-host proxy transport (imported, never modified), or a fake injected by the tests."""
import importlib.util, json, os, sys, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools", "gs"))
from gs import read_only_graphql  # noqa: E402
from common import Refused  # noqa: E402

FIRE_PATH = os.path.join(REPO, "qa", "b3-stress-harness", "b3stress", "fire.py")
APPS = {
    "stress_driver": {"env_id": "QA_STRESS_SHOPIFY_CLIENT_ID", "env_secret": "QA_STRESS_SHOPIFY_CLIENT_SECRET", "version": "2025-10",
                      "read_only": False},
    "allocator": {"env_id": "SHOPIFY_CLIENT_ID", "env_secret": "SHOPIFY_CLIENT_SECRET", "version": "2026-07", "read_only": True},
}
TOKEN_PATH = "/admin/oauth/access_token"


def load_fire():
    spec = importlib.util.spec_from_file_location("fire_ro", FIRE_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def real_transport():
    return load_fire().ShopifyTransport()


def throttle_of(body):
    c = ((body or {}).get("extensions") or {}).get("cost") or {}
    t = c.get("throttleStatus") or {}
    return {"requested": c.get("requestedQueryCost"), "actual": c.get("actualQueryCost"),
            "available": t.get("currentlyAvailable"), "maximum": t.get("maximumAvailable"), "restore": t.get("restoreRate")}


def is_throttled(body):
    return any(((e or {}).get("extensions") or {}).get("code") == "THROTTLED" for e in (body or {}).get("errors") or [])


class Client:
    def __init__(self, app, documents, env=None, transport=None):
        if app not in APPS:
            raise Refused(f"unknown app {app}")
        self.app, self.cfg = app, APPS[app]
        self.documents = frozenset(documents)
        if self.cfg["read_only"]:
            for d in self.documents:
                ok, why = read_only_graphql(d)
                if not ok:
                    raise Refused(f"{app} client is read-only: {why}")
        self.tx = transport or real_transport()
        self.path = f"/admin/api/{self.cfg['version']}/graphql.json"
        env = os.environ if env is None else env
        cid, sec = env.get(self.cfg["env_id"]), env.get(self.cfg["env_secret"])
        if not cid or not sec:
            raise Refused(f"{self.cfg['env_id']} / {self.cfg['env_secret']} not set")
        self.secrets = [cid, sec]
        c = self.tx.open()
        try:
            st, _, raw = c.request("POST", TOKEN_PATH, urllib.parse.urlencode(
                {"client_id": cid, "client_secret": sec, "grant_type": "client_credentials"}),
                {"Content-Type": "application/x-www-form-urlencoded"})
        finally:
            c.close()
        try:
            j = json.loads(raw)
        except Exception:
            raise Refused(f"token exchange: HTTP {st}, non-JSON")
        if st != 200 or not j.get("access_token"):
            raise Refused(f"token exchange failed: HTTP {st}")
        self.token, self.scope = j["access_token"], j.get("scope")
        self.secrets.append(self.token)

    def scopes(self):
        return set(str(self.scope or "").replace(",", " ").split())

    def body(self, document, variables=None):
        if document not in self.documents:
            raise Refused("document not in this client's allow-list")
        if self.cfg["read_only"] and not read_only_graphql(document)[0]:
            raise Refused("read-only client")
        d = {"query": document}
        if variables is not None:
            d["variables"] = variables
        return json.dumps(d)

    def post(self, document, variables=None):
        """One request on a fresh connection (no connection is ever reused after a failure). Returns (status, headers, body);
        raises on a transport error AFTER the request may have been sent - callers treat that as an UNKNOWN outcome."""
        b = self.body(document, variables)
        c = self.tx.open()
        try:
            st, h, raw = c.request("POST", self.path, b, {"Content-Type": "application/json", "X-Shopify-Access-Token": self.token})
        finally:
            c.close()
        try:
            return st, h, json.loads(raw)
        except Exception:
            return st, h, {"errors": [{"message": "non-JSON response"}]}
