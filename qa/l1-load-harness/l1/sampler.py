#!/usr/bin/env python3
"""H2 sampler.py: the allocator app's Shopify bucket (throttleStatus) every 2 s, and its webhook subscriptions every 30 s, from
ONE read-only query each. This is the only allocator-app read permitted during the L1 load window (H8). Its own cost is recorded
on every sample (actualQueryCost) so loadmetrics can subtract it from the bucket arithmetic.

  sampler.py run --state-dir DIR --expect-subs SUBS.json [--every 2] [--until-file FILE]     (Stage 4 only)
Writes DIR/sampler.jsonl: {ts, available, maximum, restore, requested, actual, throttled, subs?, subs_ok?, error?}.
A subscription snapshot that differs from the expected one (missing, extra, or a callback changed) is written as subs_ok=false;
guardl turns that into a SAFETY STOP. Never prints or stores the token."""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import SAMPLER_EVERY_S, SUBSCRIPTION_EVERY_S, canonical, scrub  # noqa: E402
from shop import is_throttled, throttle_of  # noqa: E402

SAMPLE_DOC = "{ shop { id } }"
SUBS_DOC = ("{ shop { id } webhookSubscriptions(first: 20) { nodes { id topic endpoint { __typename "
            "... on WebhookHttpEndpoint { callbackUrl } } } } }")
DOCUMENTS = (SAMPLE_DOC, SUBS_DOC)


def subs_of(body):
    nodes = ((((body or {}).get("data") or {}).get("webhookSubscriptions") or {}).get("nodes")) or []
    return sorted((n.get("id"), n.get("topic"), ((n.get("endpoint") or {}).get("callbackUrl"))) for n in nodes)


def subs_match(got, expected):
    return [list(x) for x in got] == [list(x) for x in expected]


class Sampler:
    def __init__(self, client, clock, out_path, expected_subs, every=SAMPLER_EVERY_S, subs_every=SUBSCRIPTION_EVERY_S):
        self.c, self.clock, self.out, self.expected = client, clock, out_path, expected_subs
        self.every, self.subs_every = every, subs_every
        self.next_subs = 0.0
        self.records = []

    def sample(self):
        now = self.clock.now()
        with_subs = now >= self.next_subs
        doc = SUBS_DOC if with_subs else SAMPLE_DOC
        rec = {"ts": now}
        try:
            st, _, body = self.c.post(doc)
            t = throttle_of(body)
            rec.update(http_status=st, available=t["available"], maximum=t["maximum"], restore=t["restore"],
                       requested=t["requested"], actual=t["actual"], throttled=is_throttled(body))
            if with_subs and not rec["throttled"]:
                got = subs_of(body)
                rec.update(subs=got, subs_ok=subs_match(got, self.expected))
                self.next_subs = now + self.subs_every
        except Exception as e:
            rec.update(error=type(e).__name__, available=None)
        self.records.append(rec)
        if self.out:
            with open(self.out, "a") as f:
                f.write(scrub(canonical(rec), getattr(self.c, "secrets", [])) + "\n")
        return rec

    def run(self, until):
        while not until():
            t0 = self.clock.now()
            self.sample()
            self.clock.sleep(max(0.0, self.every - (self.clock.now() - t0)))


def sampler_cost(records):
    """Points the sampler itself spent (sum of actualQueryCost), and its average rate in pts/s over the records' span."""
    pts = sum(r.get("actual") or 0 for r in records)
    ts = [r["ts"] for r in records if "ts" in r]
    span = (max(ts) - min(ts)) if len(ts) > 1 else 0.0
    return {"points": pts, "samples": len(records), "span_s": span, "pts_per_s": (pts / span) if span else 0.0}


def main(argv=None):
    p = argparse.ArgumentParser(prog="sampler.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--state-dir", required=True); r.add_argument("--expect-subs", required=True)
    r.add_argument("--every", type=float, default=SAMPLER_EVERY_S); r.add_argument("--until-file")
    a = p.parse_args(argv)
    from shop import Client
    client = Client("allocator", DOCUMENTS)

    class RealClock:
        now = staticmethod(time.time)
        sleep = staticmethod(time.sleep)
    s = Sampler(client, RealClock(), os.path.join(a.state_dir, "sampler.jsonl"), json.load(open(a.expect_subs)), a.every)
    stop = a.until_file or os.path.join(a.state_dir, "sampler-stop")
    s.run(lambda: os.path.exists(stop))
    return 0


if __name__ == "__main__":
    sys.exit(main())
