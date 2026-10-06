#!/usr/bin/env python3
"""H9 loadmetrics.py: L1 throughput / stability metrics from raw evidence. Every value is labelled:
  OBSERVED  read directly off timestamps or samples
  DERIVED   computed through a model or assumption (the assumption is stated in `basis`)
The point-budget figure (2-3.6 orders/s) is a HYPOTHESIS and is reported only as `theoretical_*` (DERIVED).

Inputs (JSON): arrivals [ts] (order creation; driver SUCCESS t_done or Shopify createdAt); completions {order: ts} (the moment the
order's last entry was allocated, from D1 events); sampler [records]; invocations [{start, end, topic, status}]; throttled [ts];
governor (load.py summary['governor']); restore_rate (pts/s, default 100).
  loadmetrics.py compute --in INPUT.json --out METRICS.json"""
import argparse, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import write_json  # noqa: E402

WINDOWS = (10, 30, 60)


def obs(v, basis):
    return {"value": v, "label": "OBSERVED", "basis": basis}


def der(v, basis):
    return {"value": v, "label": "DERIVED", "basis": basis}


def per_second(ts, t0, t1):
    n = max(0, int(math.ceil(t1 - t0)))
    out = [0] * n
    for t in ts:
        i = int(t - t0)
        if 0 <= i < n:
            out[i] += 1
    return out


def rolling(series, w):
    """Rate (per s) over the trailing w seconds, at each second."""
    out, acc = [], 0
    for i, v in enumerate(series):
        acc += v
        if i >= w:
            acc -= series[i - w]
        out.append(acc / min(w, i + 1))
    return out


def peak_concurrency(intervals):
    """Sweep line over [start, end) intervals; O(n log n). Ends sort before starts at the same instant."""
    ev = sorted([(s, 1) for s, e in intervals] + [(e, -1) for s, e in intervals], key=lambda x: (x[0], x[1]))
    cur = best = 0
    for _, d in ev:
        cur += d
        best = max(best, cur)
    return best


def slope(samples):
    n = len(samples)
    if n < 2:
        return 0.0
    mt = sum(t for t, _ in samples) / n
    mv = sum(v for _, v in samples) / n
    den = sum((t - mt) ** 2 for t, _ in samples)
    return 0.0 if den == 0 else sum((t - mt) * (v - mv) for t, v in samples) / den


def points_per_order(sampler, completions_ts, throttled, restore=100.0, full_margin=50):
    """Bucket arithmetic over consecutive sampler pairs where the bucket is below full (so refill is not capped) and nothing was
    throttled: points spent = restore*dt - delta_bucket - sampler's own actual cost. Divided by orders completed in the pair."""
    recs = [r for r in sampler if r.get("available") is not None]
    spent = orders = 0.0
    for a, b in zip(recs, recs[1:]):
        mx = a.get("maximum") or 2000
        if a["available"] >= mx - full_margin or b["available"] >= mx - full_margin:
            continue
        if any(a["ts"] <= t <= b["ts"] for t in throttled):
            continue
        dt = b["ts"] - a["ts"]
        spent += restore * dt - (b["available"] - a["available"]) - (b.get("actual") or 0)
        orders += sum(1 for t in completions_ts if a["ts"] < t <= b["ts"])
    return (spent / orders) if orders else None, orders


def compute(inp):
    arr = sorted(inp.get("arrivals") or [])
    comp = sorted((inp.get("completions") or {}).values())
    if not arr:
        return {"error": "no arrivals"}
    t0 = min(arr + comp)
    t1 = max(arr + comp) + 1
    a_s, c_s = per_second(arr, t0, t1), per_second(comp, t0, t1)
    m = {"window": obs([t0, t1], "first arrival to last completion")}
    for w in WINDOWS:
        m[f"arrival_rate_{w}s_peak"] = obs(max(rolling(a_s, w)), f"max trailing-{w}s arrival rate (orders/s)")
        m[f"allocation_rate_{w}s_peak"] = obs(max(rolling(c_s, w)), f"max trailing-{w}s completed-order rate (orders/s)")
    m["allocation_rate_instant_peak"] = obs(max(c_s), "max orders completed within one second")
    m["observed_peak_throughput"] = obs(max(rolling(c_s, 10)), "max trailing-10s completed-order rate (orders/s)")
    backlog, b, series = 0, 0, []
    for i in range(len(a_s)):
        b += a_s[i] - c_s[i]
        series.append(b)
    m["peak_backlog"] = obs(max(series), "max over time of (orders created - orders fully allocated)")
    m["backlog_series"] = obs(series, "per second")
    # time to first processing and per-order latency
    lat = sorted(c - a for a, c in zip(arr, comp)) if len(arr) == len(comp) else []
    if lat:
        m["latency_p50_s"] = obs(lat[len(lat) // 2], "order creation -> fully allocated (rank-matched)")
        m["latency_p95_s"] = obs(lat[int(len(lat) * 0.95) - 1], "rank-matched")
    m["drain_time_s"] = obs((max(comp) - max(arr)) if comp else None, "last arrival -> last completion")
    m["total_processing_s"] = obs((max(comp) - min(arr)) if comp else None, "first arrival -> last completion")
    # bucket and points
    smp = inp.get("sampler") or []
    av = [(r["ts"], r["available"]) for r in smp if r.get("available") is not None]
    m["bucket_min"] = obs(min((v for _, v in av), default=None), "allocator app throttleStatus.currentlyAvailable")
    m["bucket_series"] = obs(av, "sampler, every 2 s")
    sc = sum(r.get("actual") or 0 for r in smp)
    span = (av[-1][0] - av[0][0]) if len(av) > 1 else 0
    m["sampler_cost"] = obs({"points": sc, "pts_per_s": (sc / span) if span else 0.0}, "sum of the sampler's actualQueryCost")
    thr = inp.get("throttled") or []
    m["throttled_events"] = obs(len(thr), "THROTTLED retry log lines")
    ppo, n = points_per_order(smp, comp, thr, inp.get("restore_rate", 100.0))
    m["points_per_order"] = der(ppo, f"bucket arithmetic over {int(n)} orders in below-full, unthrottled sampler intervals; "
                                     "assumes refill at the restore rate and no other consumer of the allocator app's bucket")
    m["theoretical_capacity_orders_per_s"] = der((inp.get("restore_rate", 100.0) / ppo) if ppo else None,
                                                 "restore rate / points per order (point-budget estimate, not a measurement)")
    # stability point from the governor's step log (OBSERVED: each step's verdict is a measurement)
    g = inp.get("governor") or {}
    br = g.get("stability_bracket")
    m["stability_point"] = obs(br, "governor staircase: highest step that passed, first step that failed or warned")
    steps = g.get("steps") or []
    passed = [s["rate"] for s in steps if s.get("result") == "PASS"]
    m["observed_sustainable_throughput"] = obs(max(passed) if passed else None,
                                               "highest staircase rate whose step passed (flat bucket, no THROTTLED, completions >= 95%)")
    # backlog growth above the stability point: during failing/warning steps, slope of the bucket and of the backlog
    growth = []
    for s in steps:
        if s.get("result") in ("FAIL", "WARNING"):
            te = s["t"]
            win = [(t, v) for t, v in av if te - 40 <= t <= te]
            bwin = [(t0 + i, series[i]) for i in range(len(series)) if te - 40 <= t0 + i <= te]
            growth.append({"rate": s["rate"], "bucket_slope_pts_per_s": slope(win), "backlog_slope_orders_per_s": slope(bwin)})
    m["backlog_growth_above_stability"] = obs(growth, "per failing/warning step: bucket slope and backlog slope")
    if ppo:
        cap = inp.get("restore_rate", 100.0) / ppo
        m["backlog_growth_model"] = der({str(r): round(r - cap, 3) for r in (2.0, 3.0, 4.0, 5.0) if r > cap},
                                        "arrival rate - theoretical capacity, once the bucket is empty (model)")
    # drain rate once arrivals stop
    if comp and arr:
        tail = [t for t in comp if t > max(arr)]
        dur = (max(tail) - max(arr)) if tail else 0
        m["drain_rate_orders_per_s"] = obs((len(tail) / dur) if dur else None, "orders completed after the last arrival / time")
    inv = inp.get("invocations") or []
    m["peak_worker_concurrency"] = obs(peak_concurrency([(x["start"], x["end"]) for x in inv]), "Workers Logs invocation intervals")
    wh = [x for x in inv if x.get("topic")]
    m["peak_webhook_concurrency"] = obs(peak_concurrency([(x["start"], x["end"]) for x in wh]), "webhook invocations only")
    m["webhook_failures"] = obs(sum(1 for x in wh if not (isinstance(x.get("status"), int) and 200 <= x["status"] < 300)),
                                "non-2xx or no status")
    return m


def main(argv=None):
    p = argparse.ArgumentParser(prog="loadmetrics.py")
    p.add_argument("cmd", choices=["compute"]); p.add_argument("--in", dest="inp", required=True); p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    m = compute(json.load(open(a.inp)))
    write_json(a.out, m)
    print(json.dumps({k: v.get("value") if isinstance(v, dict) and "label" in v and not isinstance(v.get("value"), list) else "..."
                      for k, v in m.items()}, default=str)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
