#!/usr/bin/env python3
"""H10 rehearse.py: the two READ-ONLY rehearsals (a later, separately approved stage; NOT run in Stage 1). Zero mutations by
construction: both use read-only document allow-lists only.

  rehearse.py proxy --drafts IDS.json --out DIR --confirm REHEARSE-L1-READONLY-0-MUTATIONS
      The driver path through the real proxy at the staircase rates (1.0 .. 5.0/s, 20 s each), each "send" a read-only
      draftOrder probe (Stress Driver, 1 point) on existing drafts. Measures latency, request ids, pacing accuracy and connection
      behaviour at L1 rates. Proves pacing/transport only, not allocator capacity.
  rehearse.py clamp --out DIR --confirm REHEARSE-L1-READONLY-0-MUTATIONS --approve-t APPROVE-L1-T-THROTTLE-PROBE
      With the QA Worker verified at DRY_RUN "true" (so no webhook can depend on the bucket): sampler for 60 s, then up to two
      clamp windows; measures how precisely the clamp holds the allocator bucket and that it releases by 1.5 s."""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import APPROVE_T, CONFIRM_REHEARSE, STAIRCASE, Refused, canonical, write_json  # noqa: E402
from shop import read_only_graphql  # noqa: E402

PROBE = "query L1Probe($id: ID!) { draftOrder(id: $id) { id name status } }"
assert read_only_graphql(PROBE)[0]
STEP_S = 20.0


def proxy(client, drafts, clock, out_dir, confirm, rates=STAIRCASE, step_s=STEP_S):
    if confirm != CONFIRM_REHEARSE:
        raise Refused("rehearsal: confirmation phrase missing or wrong")
    if not drafts:
        raise Refused("rehearsal: no draft ids to probe")
    recs, k = [], 0
    for rate in rates:
        t_step = clock.now()
        n = int(rate * step_s)
        for j in range(n):
            target = t_step + j / rate
            clock.sleep(max(0.0, target - clock.now()))
            t0 = clock.now()
            try:
                st, h, body = client.post(PROBE, {"id": drafts[k % len(drafts)]})
                recs.append({"rate": rate, "t": t0, "lat": clock.now() - t0, "status": st, "rid": bool((h or {}).get("x-request-id"))})
            except Exception as e:
                recs.append({"rate": rate, "t": t0, "error": type(e).__name__})
            k += 1
    os.makedirs(out_dir, exist_ok=True)
    summ = {}
    for rate in rates:
        rs = [r for r in recs if r["rate"] == rate]
        ts = [r["t"] for r in rs]
        lat = sorted(r["lat"] for r in rs if "lat" in r)
        summ[str(rate)] = {"n": len(rs), "achieved_rate": (len(ts) - 1) / (ts[-1] - ts[0]) if len(ts) > 1 and ts[-1] > ts[0] else None,
                           "errors": sum(1 for r in rs if "error" in r), "no_request_id": sum(1 for r in rs if r.get("rid") is False),
                           "lat_p50": lat[len(lat) // 2] if lat else None, "lat_max": lat[-1] if lat else None}
    write_json(os.path.join(out_dir, "rehearse-proxy.json"), {"records": recs, "summary": summ})
    return summ


def clamp_rehearsal(client, clock, out_dir, confirm, approve_t, dry_run_value, sample_s=60.0):
    from clamp import Clamp
    from sampler import DOCUMENTS, Sampler
    if confirm != CONFIRM_REHEARSE:
        raise Refused("rehearsal: confirmation phrase missing or wrong")
    if approve_t != APPROVE_T:
        raise Refused("rehearsal: the clamp rehearsal needs the T approval phrase")
    if dry_run_value() != "true":
        raise Refused("rehearsal: QA Worker DRY_RUN must be exactly \"true\" for the clamp rehearsal")
    os.makedirs(out_dir, exist_ok=True)
    s = Sampler(client, clock, os.path.join(out_dir, "rehearse-sampler.jsonl"), [])
    t_end = clock.now() + sample_s
    s.run(lambda: clock.now() >= t_end)
    log = []
    c = Clamp(client, clock, lambda: "T", approve_t, None, None, log)
    for _ in range(2):
        c.window("T")
        t_end = clock.now() + 20
        s.run(lambda: clock.now() >= t_end)
    write_json(os.path.join(out_dir, "rehearse-clamp.json"), {"windows": log, "samples": len(s.records)})
    return log


def main(argv=None):
    p = argparse.ArgumentParser(prog="rehearse.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("proxy"); x.add_argument("--drafts", required=True); x.add_argument("--out", required=True); x.add_argument("--confirm")
    y = sub.add_parser("clamp"); y.add_argument("--out", required=True); y.add_argument("--confirm"); y.add_argument("--approve-t")
    a = p.parse_args(argv)

    class RealClock:
        now = staticmethod(time.time)
        sleep = staticmethod(time.sleep)
    try:
        if a.confirm != CONFIRM_REHEARSE:
            raise Refused("confirmation phrase missing or wrong")
        from shop import Client, load_fire
        if a.cmd == "proxy":
            r = proxy(Client("stress_driver", {PROBE}), json.load(open(a.drafts)), RealClock(), a.out, a.confirm)
        else:
            from clamp import CLAMP_DOC
            from sampler import DOCUMENTS
            fire = load_fire()

            def dry():
                acct, tok = os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_API_TOKEN")
                st, s = fire.cf_json(fire.CloudflareTransport(), f"/client/v4/accounts/{acct}/workers/scripts/{fire.QA_WORKER}/settings", tok)
                v = [b.get("text") for b in (s.get("result") or {}).get("bindings", []) if b.get("name") == "DRY_RUN"]
                return v[0] if v == ["true"] else repr(v)
            r = clamp_rehearsal(Client("allocator", set(DOCUMENTS) | {CLAMP_DOC}), RealClock(), a.out, a.confirm, a.approve_t, dry)
        print(canonical(r)[:2000])
        return 0
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
