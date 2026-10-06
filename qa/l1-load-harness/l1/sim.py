"""Offline world model for L1 tests and `load.py simulate` (no network). It is a MODEL, not evidence: it lets the governor,
driver and failwatch be exercised against a leaky-bucket allocator whose cost per order is a parameter.

Allocator model: each order's two webhooks share one Shopify app bucket (max 2000, restore 100 pts/s). An order is processed
`latency_s` after it is created; processing needs `requested` points available and then spends `cost` points. If the bucket is
short, the Worker's in-process retries wait up to 1.5 s (THROTTLED logged on each retry); still short -> the delivery fails (HTTP
500) and Shopify retries it `shopify_retry_s` later."""
import itertools

from common import SAMPLER_EVERY_S
from failwatch import FailWatch
from governor import Signals


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = float(t)

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class World:
    def __init__(self, clock, cost=28.0, requested=31.0, latency_s=1.5, restore=100.0, bucket_max=2000.0,
                 retry_window_s=1.5, shopify_retry_s=60.0, wall_ms=900):
        self.clock, self.cost, self.requested, self.latency = clock, cost, requested, latency_s
        self.restore, self.max, self.retry_window, self.shopify_retry = restore, bucket_max, retry_window_s, shopify_retry_s
        self.wall_ms = wall_ms
        self.bucket, self.t_last = bucket_max, clock.now()
        self.pending = []           # [due, first_try, index, attempt]
        self.created, self.completed = [], []
        self.throttled, self.samples = [], []
        self.fw = FailWatch()
        self.ids = itertools.count(1)
        self.next_sample = clock.now()
        self.min_bucket = bucket_max
        self.extra_drain = 0.0       # pts/s drained by something else (used to model a clamp)

    def arrive(self, index):
        now = self.clock.now()
        self.created.append((now, index))
        self.pending.append([now + self.latency, None, index, 0])

    def advance(self):
        now = self.clock.now()
        dt = now - self.t_last
        if dt > 0:
            self.bucket = min(self.max, self.bucket + (self.restore - self.extra_drain) * dt)
            self.bucket = max(0.0, self.bucket)
            self.t_last = now
        still = []
        for p in sorted(self.pending):
            due, first, index, attempt = p
            if due > now:
                still.append(p)
                continue
            if self.bucket >= self.requested:
                self.bucket -= self.cost
                self.completed.append((now, index))
                self.fw.observe({"ts": now, "topic": "orders/paid", "webhook_id": f"w{index}-{attempt}", "status": 200,
                                 "wall_ms": self.wall_ms, "request_id": f"r{next(self.ids)}"})
                continue
            self.throttled.append(now)
            first = first if first is not None else now
            if now - first >= self.retry_window:
                self.fw.observe({"ts": now, "topic": "orders/paid", "webhook_id": f"w{index}", "status": 500,
                                 "wall_ms": int(self.retry_window * 1000), "request_id": f"r{next(self.ids)}"})
                still.append([now + self.shopify_retry, None, index, attempt + 1])
            else:
                still.append([now + 0.5, first, index, attempt])
        self.pending = still
        self.min_bucket = min(self.min_bucket, self.bucket)
        while self.next_sample <= now:
            self.samples.append((self.next_sample, int(self.bucket)))
            self.next_sample += SAMPLER_EVERY_S

    def signals(self, now, driver_arrivals=None):
        self.advance()
        arr = driver_arrivals if driver_arrivals is not None else [t for t, _ in self.created]
        return Signals(bucket=self.samples[-60:], throttled=[t for t in self.throttled if t >= now - 30],
                       wall_ms_max=self.wall_ms, fw_level=self.fw.level,
                       arr_shifted30=sum(1 for t in arr if now - 35 <= t <= now - 5),
                       done30=sum(1 for t, _ in self.completed if now - 30 <= t <= now),
                       drained=not self.pending and bool(self.created))


class SimClient:
    """Stands in for the Stress Driver client: every completion succeeds with a request id and creates an order in the World."""
    MUTATION_SEEN = []

    def __init__(self, world, fail=None):
        self.world, self.fail, self.calls = world, fail or {}, []
        self.secrets = []

    def post(self, document, variables=None):
        did = (variables or {}).get("id")
        self.calls.append(did)
        mode = self.fail.get(did)
        if mode == "raise":
            raise OSError("simulated connection reset after send")
        if mode == "norid":
            return 200, {}, {"data": {"draftOrderComplete": {"draftOrder": {"id": did, "status": "COMPLETED"}, "userErrors": []}}}
        if mode == "throttled":
            return 200, {"x-request-id": f"req-{len(self.calls)}"}, {"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}]}
        if mode == "usererror":
            return 200, {"x-request-id": f"req-{len(self.calls)}"}, {"data": {"draftOrderComplete": {"draftOrder": None,
                                                                                         "userErrors": [{"message": "x"}]}}}
        self.world.arrive(int(did.rsplit("/", 1)[1]))
        return 200, {"x-request-id": f"req-{len(self.calls)}"}, {
            "data": {"draftOrderComplete": {"draftOrder": {"id": did, "name": "#D", "status": "COMPLETED"}, "userErrors": []}},
            "extensions": {"cost": {"requestedQueryCost": 10, "actualQueryCost": 10,
                                    "throttleStatus": {"maximumAvailable": 2000.0, "currentlyAvailable": 1900, "restoreRate": 100.0}}}}
