#!/usr/bin/env python3
"""B3 STRESS-window state checker (tails only: webhooks + sweeps).
Usage: cstate.py DIR SINCE_MS TARGETVER  -> prints JSON {hooks, sweeps, orders, per_topic, hooks_done, zero_sweeps_after_complete, claimed_by_sweeps, deviations}
Config: DIR/stress.json. Binding: DIR/bound-orders.txt (one order gid per line, written by guard.sh from Shopify) when present.
Sources: the tails (pre, pre2, conf) and obs.jsonl (Workers Logs invocations recovered for a tail gap by evidence.py; same checks).
Webhooks: every delivery since SINCE_MS must be 200 / ok / 0 exceptions / signed (HMAC header) / shop + API version / on TARGETVER /
processed (exactly one webhook_processed log with lines=1, nothing else), arrive after qag-start (armed), be orders/create or
orders/paid, and come from at most len(order_quantities) distinct orders (all within the bound set once bound).
Duplicate deliveries are tolerated (D1 proves they changed nothing). hooks_done = every bound order has both topics.
Sweeps (live): on TARGETVER, live, errors/unreadable/refused/released/aged 0, not truncated, no unexpected logs; may converge
(claim) only orders in the webhook/bound set, only on the stress competition, never before arming, never release; claims across
all sweeps <= capacity. Every sweep scheduled after the pool was observed complete (DIR/complete-ms) must be all zeros.
zero_sweeps_after_complete counts QUALIFYING sweeps: */15 trailing, after complete-ms, all zeros, in_scope >= number of orders."""
import json, os, subprocess, sys
D, since, tgt = sys.argv[1], int(sys.argv[2]), sys.argv[3]
cfg = json.load(open(f'{D}/stress.json')); C = cfg['competition_id']; NORD = len(cfg['order_quantities']); CAP = cfg['capacity']
rd = lambda f: open(f'{D}/{f}').read().strip() if os.path.exists(f'{D}/{f}') else None
armed = int(rd('qag-start') or 0) or None
done_ms = int(rd('complete-ms') or 0) or None
bound = set(l.strip() for l in (rd('bound-orders.txt') or '').splitlines() if l.strip())
dev = []

def tail_events():
    seen = {}
    for f in ('pre.jsonl', 'pre2.jsonl', 'conf.jsonl', 'obs.jsonl'):
        try: txt = open(f'{D}/{f}', encoding='utf-8', errors='replace').read()
        except FileNotFoundError: continue
        dec, i = json.JSONDecoder(), 0
        while True:
            j = txt.find('{', i)
            if j < 0: break
            try: o, e = dec.raw_decode(txt, j); i = e
            except ValueError: i = j + 1; continue
            if isinstance(o, dict) and o.get('eventTimestamp', 0) >= since:
                ray = (((o.get('event') or {}).get('request') or {}).get('headers') or {}).get('cf-ray')
                seen.setdefault(('ray', ray) if ray else (o['eventTimestamp'], json.dumps(o.get('event'), sort_keys=True)[:400]), o)
    return sorted(seen.values(), key=lambda o: o['eventTimestamp'])

def logs_of(o):
    out = []
    for l in o.get('logs', []):
        for m in l.get('message', []):
            try: out.append(json.loads(m))
            except Exception: out.append(m)
    return out

hooks = []; per = {}; orders = set()
for o in tail_events():
    req = (o.get('event') or {}).get('request') or {}; u = req.get('url') or ''
    if '/webhooks/' not in u: continue
    h = req.get('headers', {}); lg = logs_of(o); ts = o['eventTimestamp']
    proc = [m for m in lg if isinstance(m, dict) and m.get('event') == 'webhook_processed']
    bad = [m for m in lg if not isinstance(m, dict) or m.get('level') in ('warn', 'error') or m.get('event') not in ('webhook_processed',)]
    og = next((m.get('order_gid') for m in lg if isinstance(m, dict) and m.get('order_gid')), None)
    x = {'ts': ts, 'wall': o.get('wallTime'), 'topic': h.get('x-shopify-topic'), 'order': og, 'status': ((o.get('event') or {}).get('response') or {}).get('status'),
         'version': ((o.get('scriptVersion') or {}).get('id') or '')[:8], 'webhook_id': h.get('x-shopify-webhook-id'), 'triggered_at': h.get('x-shopify-triggered-at'),
         'hmac_header': bool(h.get('x-shopify-hmac-sha256')), 'path': u.split('.dev', 1)[-1], 'lines': proc[0].get('lines') if proc else None,
         'source': o.get('_source', 'tail')}
    hooks.append(x); p = []
    if x['status'] != 200 or o.get('outcome') != 'ok' or o.get('exceptions'): p.append(f"status {x['status']} outcome {o.get('outcome')} exc {len(o.get('exceptions', []))}")
    if ((o.get('scriptVersion') or {}).get('id')) != tgt: p.append(f"version {x['version']}")
    if not h.get('x-shopify-hmac-sha256') or h.get('x-shopify-shop-domain') != '7r5csb-1j.myshopify.com' or h.get('x-shopify-api-version') != '2026-07': p.append('headers')
    if len(proc) != 1 or bad: p.append(f'logs {lg}')
    if not armed or ts < armed - 2000: p.append('webhook before the stress test was armed')
    if x['topic'] not in ('orders/create', 'orders/paid'): p.append(f"unexpected topic {x['topic']}")
    if not og: p.append('no order_gid')
    else:
        orders.add(og)
        if bound and og not in bound: p.append(f'order {og} is not one of the bound stress orders')
    if proc and proc[0].get('lines') != 1: p.append(f"webhook_processed lines={proc[0].get('lines')} (expected 1)")
    k = (og, x['topic']); per[k] = per.get(k, 0) + 1
    if p: dev.append(f"webhook {x['topic']} {og} {x['webhook_id']}: {p}")
if len(orders) > NORD: dev.append(f'webhooks for {len(orders)} distinct orders (max {NORD})')
known = bound | orders
hooks_done = len(bound) == NORD and all(per.get((o, t), 0) >= 1 for o in bound for t in ('orders/create', 'orders/paid'))

out = subprocess.run(['python3', f'{D}/sweeps.py', D, str(since)], capture_output=True, text=True).stdout
sw = [json.loads(l) for l in out.splitlines() if l.strip()]
sweeps = []; n = 0; claimed_total = 0; zero_after = 0
for s in sw:
    lg = [m for m in s['logs'] if isinstance(m, dict)]
    summ = next((m for m in lg if m.get('event') == 'reconcile_summary'), None)
    if summ is None: continue
    n += 1; p = []
    conv = [m for m in lg if m.get('event') == 'reconcile_converged']
    other = [m for m in s['logs'] if not isinstance(m, dict) or m.get('event') not in ('reconcile_started', 'reconcile_summary', 'reconcile_converged')]
    started = s.get('scheduledTime') or s['ts']
    if s['version'] != tgt: p.append(f"version {s['version']}")
    if s['outcome'] != 'ok' or s['exceptions']: p.append(f"outcome {s['outcome']} exc {len(s['exceptions'])}")
    if summ.get('dry_run') is not False or summ.get('report') is not False: p.append('not live')
    for k in ('errors', 'unreadable', 'refused_not_open', 'released', 'aged_held'):
        if summ.get(k) != 0: p.append(f'{k}={summ.get(k)}')
    if summ.get('truncated'): p.append('truncated')
    if other: p.append(f'unexpected logs {other}')
    if conv and (not armed or started < armed - 2000): p.append('sweep changed state before the stress test was armed')
    claimed = 0
    for c in conv:
        if c.get('order_gid') not in known: p.append(f"converged order {c.get('order_gid')} is not a stress order")
        for oc in c.get('outcomes', []):
            claimed += len(oc.get('claimed') or [])
            if oc.get('released'): p.append(f"released {oc.get('released')}")
            if oc.get('competition_id') != C and (oc.get('claimed') or oc.get('released')): p.append(f"change on competition {oc.get('competition_id')}")
    if summ.get('claimed') != claimed: p.append(f"summary claimed {summ.get('claimed')} vs lines {claimed}")
    if summ.get('mismatched') != summ.get('converged_orders') or summ.get('mismatched', 0) > NORD: p.append(f"mismatched {summ.get('mismatched')} / converged {summ.get('converged_orders')}")
    claimed_total += claimed
    if done_ms and started > done_ms:
        for k in ('mismatched', 'converged_orders', 'claimed'):
            if summ.get(k) != 0: p.append(f'{k}={summ.get(k)} after the pool was complete')
        if conv: p.append(f'converged lines after the pool was complete {conv}')
        if not p and s['cron'] == '*/15 * * * *' and summ.get('mode') == 'trailing' and (summ.get('in_scope') or 0) >= NORD: zero_after += 1
    sweeps.append({'n': n, 'ts': s['ts'], 'cron': s['cron'], 'version': s['version'][:8], 'run_id': summ.get('run_id'), 'mode': summ.get('mode'),
                   'after_complete': bool(done_ms and started > done_ms), 'search': summ.get('search'),
                   'summary': {k: summ.get(k) for k in ('orders_seen', 'in_scope', 'mismatched', 'converged_orders', 'claimed', 'released', 'refused_not_open', 'unreadable', 'errors', 'truncated', 'aged_held')},
                   'converged': [{'order': c.get('order_gid', '')[-14:], 'reason': c.get('reason'), 'outcomes': c.get('outcomes')} for c in conv], 'deviations': p})
    if p: dev.append(f'sweep {n} ({summ.get("run_id")}): {p}')
if claimed_total > CAP: dev.append(f'sweeps claimed {claimed_total} entries in total (max {CAP})')
print(json.dumps({'hooks': hooks, 'sweeps': sweeps, 'orders': sorted(orders), 'per_topic': {f'{k[0]}|{k[1]}': v for k, v in per.items()}, 'hooks_done': hooks_done,
                  'zero_sweeps_after_complete': zero_after, 'claimed_by_sweeps': claimed_total, 'deviations': dev}))
