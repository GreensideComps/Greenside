#!/usr/bin/env python3
"""VALIDATION TOOL ONLY (not harness). Read-only paged Workers Logs fetch for the QA Worker.
wlfetch(from_ms, to_ms, extra_filters) -> (records, series_total, pages). Pages newest->oldest with limit 2000 and
offset=<last $metadata.id>, offsetDirection=next. Completeness check: len(records) must equal the series bucket total."""
import json, os, subprocess, time
URL = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID']}/workers/observability/telemetry/query"
SVC = {'key': '$metadata.service', 'operation': 'eq', 'type': 'string', 'value': 'greenside-entry-allocator-qa'}
def query(body):
    r = subprocess.run(['curl', '-sS', '-X', 'POST', '-H', f"Authorization: Bearer {os.environ['CLOUDFLARE_API_TOKEN']}", '-H', 'Content-Type: application/json',
                        URL, '--data', json.dumps(body)], capture_output=True, text=True, timeout=60)
    o = json.loads(r.stdout)
    if not o.get('success'): raise RuntimeError(f"query failed: {str(o)[:300]}")
    return o
def wlfetch(t0, t1, extra=(), limit=2000, tag='b3-val'):
    recs, off, pages, totals = [], None, 0, set()
    while True:
        b = {'queryId': f'{tag}-{pages}', 'timeframe': {'from': t0, 'to': t1}, 'view': 'events', 'limit': limit, 'parameters': {'filters': [SVC, *extra]}}
        if off: b.update(offset=off, offsetDirection='next')
        o = query(b); ev = o['result']['events']['events']; pages += 1
        totals.add(sum(d['count'] for s in o['result']['events']['series'] for d in s['data']))
        recs += ev
        if len(ev) < limit: break
        off = ev[-1]['$metadata']['id']
    return recs, totals, pages
