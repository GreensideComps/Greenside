#!/usr/bin/env python3
"""H3 clamp.py: the phase-T read-only "bucket clamp". OPTIONAL; never part of the default L1 path (A -> B -> C -> E).

It holds the ALLOCATOR app's Shopify bucket low for a short window by sending ONE fixed read-only query back to back, so the real
allocator meets real THROTTLED responses while only about 1-2 webhooks are in flight. It cannot mutate anything:
  - the only document it can send is CLAMP_DOC, checked by tools/gs read_only_graphql at import and before every send;
  - it takes no document, variables or host from its caller;
  - it runs only with the T approval phrase (T2 windows only with the T2 phrase as well), only while the governor's phase is T
    (or T2 for the T2 window), and at most CLAMP_T_WINDOWS windows in T and one in T2.
A window releases at once on the first THROTTLED (its own response, or one reported by failwatch/guard via `throttled_seen`) or
when it reaches its hard maximum (1.5 s in T; 3.0 s in T2). No clamp request is ever STARTED at or after the deadline; requests
are sequential (one at a time)."""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import APPROVE_T, APPROVE_T2, CLAMP_MAX_WINDOW_S, CLAMP_T2_MAX_WINDOW_S, CLAMP_T_WINDOWS, Refused  # noqa: E402
from shop import is_throttled, read_only_graphql, throttle_of  # noqa: E402

CLAMP_DOC = ("{ orders(first: 40, sortKey: CREATED_AT, reverse: true) { nodes { id name lineItems(first: 10) "
             "{ nodes { id quantity currentQuantity } } } } }")
_ok, _why = read_only_graphql(CLAMP_DOC)
assert _ok, _why


class Clamp:
    def __init__(self, client, clock, phase_fn, approve_t=None, approve_t2=None, throttled_seen=None, log=None):
        if approve_t != APPROVE_T:
            raise Refused("clamp: T is not approved (phrase missing or wrong); the default L1 path never clamps")
        if approve_t2 not in (None, APPROVE_T2):
            raise Refused("clamp: T2 approval phrase is wrong")
        if getattr(client, "app", "allocator") != "allocator":
            raise Refused("clamp: only the allocator app's read-only client may be used")
        self.c, self.clock, self.phase_fn = client, clock, phase_fn
        self.t2_ok = approve_t2 == APPROVE_T2
        self.throttled_seen = throttled_seen or (lambda since: False)
        self.windows = {"T": 0, "T2": 0}
        self.log = log if log is not None else []

    def window(self, kind="T"):
        if kind not in ("T", "T2"):
            raise Refused(f"clamp: unknown window kind {kind}")
        if kind == "T2" and not self.t2_ok:
            raise Refused("clamp: T2 is not approved")
        phase = self.phase_fn()
        if phase != kind:
            raise Refused(f"clamp: refused outside phase {kind} (governor phase is {phase})")
        limit = CLAMP_T_WINDOWS if kind == "T" else 1
        if self.windows[kind] >= limit:
            raise Refused(f"clamp: {kind} window budget ({limit}) used")
        self.windows[kind] += 1
        max_s = CLAMP_MAX_WINDOW_S if kind == "T" else CLAMP_T2_MAX_WINDOW_S
        t0 = self.clock.now()
        rec = {"kind": kind, "t0": t0, "requests": 0, "release": None, "min_available": None}
        while True:
            now = self.clock.now()
            if now - t0 >= max_s:
                rec["release"] = "timeout"
                break
            if self.phase_fn() != kind:
                rec["release"] = "phase changed"
                break
            if self.throttled_seen(t0):
                rec["release"] = "throttled (allocator)"
                break
            if not read_only_graphql(CLAMP_DOC)[0]:      # belt and braces: never send anything but the read-only document
                raise Refused("clamp document is not read-only")
            st, _, body = self.c.post(CLAMP_DOC)
            rec["requests"] += 1
            av = throttle_of(body).get("available")
            if av is not None:
                rec["min_available"] = av if rec["min_available"] is None else min(rec["min_available"], av)
            if is_throttled(body):
                rec["release"] = "throttled (clamp's own request)"
                break
        rec["t1"] = self.clock.now()
        rec["duration_s"] = rec["t1"] - t0
        self.log.append(rec)
        return rec
