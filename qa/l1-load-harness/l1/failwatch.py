"""H4 failwatch: real-time per-topic webhook failure counters for L1 (pure logic; no network).

Shopify (verified): "After 8 consecutive failures, the subscription is automatically deleted if it was configured using the
Admin API." The allocator's five subscriptions are Admin-API created. Whether "consecutive" is per event or per subscription is not
documented, so the STRICT reading is assumed: consecutive across all events of one topic.

A failed delivery = the Worker answered non-2xx, gave no status, or took longer than Shopify's 5 s timeout.

Levels only ever rise:  NONE < WARNING < LOAD_STOP < SAFETY_STOP
  LOAD_STOP    the first failed delivery on any topic (outside an approved T2 single-failure budget)
  SAFETY_STOP  2 consecutive failures on one topic | 3 failures in total on one topic | the same webhook-id failing twice |
               a failure more than 30 s after a LOAD STOP
Sources: the live tail (fast) and Workers Logs (authoritative). The same invocation reported by both is counted once (key: the
invocation's request id, else webhook-id + topic + start time). A Workers Logs invocation the tail missed is added; a tail-reported
failure Workers Logs cannot confirm is still counted (conservative) and listed as unconfirmed."""
from common import (FAIL_AFTER_LOAD_STOP_S, SAFETY_CONSECUTIVE, SAFETY_TOTAL, SHOPIFY_DELETE_AFTER, SHOPIFY_TIMEOUT_MS)

LEVELS = ("NONE", "WARNING", "LOAD_STOP", "SAFETY_STOP")
assert SAFETY_CONSECUTIVE < SHOPIFY_DELETE_AFTER and SAFETY_TOTAL < SHOPIFY_DELETE_AFTER


def is_failure(ev):
    st = ev.get("status")
    if not isinstance(st, int) or not 200 <= st < 300:
        return True
    w = ev.get("wall_ms")
    return isinstance(w, (int, float)) and w > SHOPIFY_TIMEOUT_MS


def _key(ev):
    if ev.get("request_id"):
        return ("rid", ev["request_id"])
    return ("wh", ev.get("webhook_id"), ev.get("topic"), round(float(ev.get("ts", 0)), 1))


class FailWatch:
    def __init__(self, t2_approved=False):
        self.t2_approved = bool(t2_approved)
        self.t2_budget = 1 if self.t2_approved else 0
        self.level = "NONE"
        self.reasons = []
        self.seen = {}
        self.consecutive, self.total, self.successes = {}, {}, {}
        self.max_consecutive = {}
        self.fail_by_webhook = {}
        self.failures = []
        self.load_stop_at = None
        self.unconfirmed = []

    # -- level handling
    def _raise(self, level, why):
        if LEVELS.index(level) > LEVELS.index(self.level):
            self.level = level
        self.reasons.append(f"{level}: {why}")

    def note_load_stop(self, ts):
        """The driver stopped sending (for any reason). Failures well after this mean throttling is not clearing: SAFETY."""
        if self.load_stop_at is None:
            self.load_stop_at = float(ts)

    # -- events
    def observe(self, ev, source="tail"):
        """ev: {ts (epoch s), topic, webhook_id, status, wall_ms, request_id?, phase?}. Returns the level after this event."""
        k = _key(ev)
        if k in self.seen:
            self.seen[k]["sources"].add(source)
            return self.level
        self.seen[k] = {"ev": ev, "sources": {source}}
        topic = ev.get("topic") or "?"
        if not is_failure(ev):
            self.consecutive[topic] = 0
            self.successes[topic] = self.successes.get(topic, 0) + 1
            return self.level
        self.consecutive[topic] = self.consecutive.get(topic, 0) + 1
        self.total[topic] = self.total.get(topic, 0) + 1
        self.max_consecutive[topic] = max(self.max_consecutive.get(topic, 0), self.consecutive[topic])
        wid = ev.get("webhook_id")
        if wid:
            self.fail_by_webhook[wid] = self.fail_by_webhook.get(wid, 0) + 1
        self.failures.append({"ts": ev.get("ts"), "topic": topic, "webhook_id": wid, "status": ev.get("status"),
                              "wall_ms": ev.get("wall_ms"), "phase": ev.get("phase"), "source": source})
        what = f"{topic} webhook {wid} status {ev.get('status')} wall {ev.get('wall_ms')}ms"
        # SAFETY first: these hold even inside an approved T2
        if self.consecutive[topic] >= SAFETY_CONSECUTIVE:
            self._raise("SAFETY_STOP", f"{self.consecutive[topic]} consecutive failures on {topic} ({what})")
        if self.total[topic] >= SAFETY_TOTAL:
            self._raise("SAFETY_STOP", f"{self.total[topic]} failures in total on {topic} ({what})")
        if wid and self.fail_by_webhook[wid] >= 2:
            self._raise("SAFETY_STOP", f"webhook {wid} failed again (retried delivery failed): {what}")
        if self.load_stop_at is not None and float(ev.get("ts", 0)) > self.load_stop_at + FAIL_AFTER_LOAD_STOP_S:
            self._raise("SAFETY_STOP", f"failure {float(ev.get('ts', 0)) - self.load_stop_at:.1f}s after the LOAD STOP: {what}")
        if ev.get("phase") == "T2" and self.t2_budget > 0:
            self.t2_budget -= 1
            self._raise("WARNING", f"the one approved T2 failure: {what}")
        else:
            self._raise("LOAD_STOP", f"failed delivery: {what}")
        return self.level

    def confirm_wl(self, wl_events, complete):
        """Fold in a Workers Logs window. complete=False (window not provably complete) is itself a LOAD STOP: the stop rule
        cannot be evaluated. Returns the list of tail failures Workers Logs did not show."""
        if not complete:
            self._raise("LOAD_STOP", "Workers Logs window not complete: failure counting cannot be confirmed")
        wl_keys = set()
        for ev in wl_events:
            wl_keys.add(_key(ev))
            self.observe(ev, source="wl")
        missing = [v["ev"] for k, v in self.seen.items()
                   if v["sources"] == {"tail"} and is_failure(v["ev"]) and k not in wl_keys]
        self.unconfirmed = missing
        return missing

    def state(self):
        return {"level": self.level, "reasons": list(self.reasons), "failures": list(self.failures),
                "consecutive": dict(self.consecutive), "max_consecutive": dict(self.max_consecutive), "total": dict(self.total),
                "successes": dict(self.successes), "repeated_webhooks": sorted(w for w, n in self.fail_by_webhook.items() if n >= 2),
                "load_stop_at": self.load_stop_at, "t2_budget_left": self.t2_budget, "unconfirmed": list(self.unconfirmed),
                "margin_to_shopify_deletion": {t: SHOPIFY_DELETE_AFTER - n for t, n in self.max_consecutive.items()}}
