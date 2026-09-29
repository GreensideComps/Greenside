#!/usr/bin/env python3
"""Concurrency evidence for the stress window. Usage: overlap.py DIR FROM_MS TO_MS  -> prints JSON.
Each Worker invocation seen by either tail (or recovered from Workers Logs into obs.jsonl) in [FROM_MS, TO_MS] is an interval [eventTimestamp, eventTimestamp + wallTime] (the time the
isolate spent on it, including its D1 batch). Every pair of overlapping intervals is classified:
  cross_order   two webhook invocations for DIFFERENT orders in flight at the same time  (the core stress case)
  same_order    two webhook invocations for the SAME order in flight at the same time    (duplicate / create+paid race)
  hook_sweep    a webhook invocation in flight while a reconciliation sweep is running    (webhook vs reconcile race)
verdict: CONCURRENT if cross_order >= 1, otherwise SEQUENTIAL (the test then only proves sequential correctness: INCONCLUSIVE).
Also reports the peak number of invocations in flight at once."""
import json, sys, datetime
D, t0, t1 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
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
        if not isinstance(o, dict) or not (t0 <= o.get('eventTimestamp', 0) <= t1): continue
        ev = o.get('event') or {}; req = ev.get('request') or {}; u = req.get('url') or ''
        if ev.get('cron'): kind, key = 'sweep', ev['cron']
        elif '/webhooks/' in u:
            og = None
            for l in o.get('logs', []):
                for m in l.get('message', []):
                    try: mm = json.loads(m)
                    except Exception: continue
                    if isinstance(mm, dict) and mm.get('order_gid'): og = mm['order_gid']
            kind, key = 'hook', f"{(req.get('headers') or {}).get('x-shopify-topic')}|{og}"
        else: continue
        ray = ((req.get('headers') or {}).get('cf-ray')) if req else None
        k = ('ray', ray, kind, key) if ray else (o['eventTimestamp'], kind, key)
        prev = seen.get(k)
        if prev is None or (o.get('wallTime') or 0) > prev[1]: seen[k] = (o['eventTimestamp'], o.get('wallTime') or 0)
inv = sorted((ts, ts + w, k[-2], k[-1]) for k, (ts, w) in seen.items())
f = lambda ms: datetime.datetime.utcfromtimestamp(ms / 1000).strftime('%H:%M:%S.%f')[:-3]
pairs = {'cross_order': [], 'same_order': [], 'hook_sweep': []}
for i, a in enumerate(inv):
    for b in inv[i + 1:]:
        if b[0] >= a[1]: continue
        ov = min(a[1], b[1]) - b[0]
        if a[2] == 'hook' and b[2] == 'hook':
            cls = 'same_order' if a[3].split('|')[1] == b[3].split('|')[1] else 'cross_order'
        elif 'sweep' in (a[2], b[2]) and 'hook' in (a[2], b[2]): cls = 'hook_sweep'
        else: continue
        pairs[cls].append({'a': f'{f(a[0])} {a[3][-30:]}', 'b': f'{f(b[0])} {b[3][-30:]}', 'overlap_ms': ov})
peak = max((sum(1 for x in inv if x[0] <= s < x[1]) for s, *_ in inv), default=0)
print(json.dumps({'invocations': [{'start': f(s), 'ms': e - s, 'kind': k, 'key': key} for s, e, k, key in inv],
                  'counts': {k: len(v) for k, v in pairs.items()}, 'peak_in_flight': peak,
                  'verdict': 'CONCURRENT' if pairs['cross_order'] else 'SEQUENTIAL', 'pairs': pairs}))
