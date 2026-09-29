#!/usr/bin/env python3
"""Workers Logs gap-recovery source for the B3 stress harness (read-only). Tails stay the primary evidence; this module is only
used by evidence.py to examine a detected tail gap. It never deploys, writes or changes anything on Cloudflare.

fetch_window(t0_ms, t1_ms) -> dict
  Pages the Workers Observability query (QA Worker only) newest->oldest with limit 2000 and offset=<last $metadata.id>,
  offsetDirection=next, until a page returns fewer than 2000 records. The window counts as COMPLETE only if:
  every page success=true; paging reached the end (a short page); records == sum of series bucket counts; every
  sampleInterval == 1; no $workers.truncated=true; every record belongs to greenside-entry-allocator-qa.
  Returns {complete, problems, pages, records, series_total, sample_intervals, invocations, invalid}.
invocations: one entry per $metadata.type "cf-worker-event" record, converted to the tail JSON shape
  (eventTimestamp = START = timestamp - wallTimeMs; the Workers Logs timestamp is kept as END in _wl.end), with its console.log
  lines joined ONLY by $metadata.requestId (message = json.dumps(source), validated identical to the tail format).
  An invocation whose requestId carries any other record type, or a second invocation record, is listed in `invalid`.
Correlation keys: fetch = cf-ray request header; cron = (cron, scheduledTime in ms; Workers Logs stores seconds).
Transport: real HTTPS query, or OBSQ_FAKE=<json file> (offline tests only): a simulated store with the same paging, sorting,
series and sampling semantics, plus fault switches (fail_page, success_false, sample_interval, drop_series, truncated_ids)."""
import json, os, subprocess, sys
SERVICE = 'greenside-entry-allocator-qa'
LIMIT = 2000
SVC = {'key': '$metadata.service', 'operation': 'eq', 'type': 'string', 'value': SERVICE}

def _real(body):
    url = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID']}/workers/observability/telemetry/query"
    r = subprocess.run(['curl', '-sS', '--max-time', '30', '-X', 'POST', '-H', f"Authorization: Bearer {os.environ['CLOUDFLARE_API_TOKEN']}",
                        '-H', 'Content-Type: application/json', url, '--data', json.dumps(body)], capture_output=True, text=True, timeout=45)
    return json.loads(r.stdout)

_fake_calls = [0]
def _fake(body):
    st = json.load(open(os.environ['OBSQ_FAKE'])); _fake_calls[0] += 1
    if os.environ.get('OBSQ_CALLLOG'):
        with open(os.environ['OBSQ_CALLLOG'], 'a') as fh: fh.write(json.dumps(body) + '\n')
    if st.get('success_false'): return {'success': False, 'errors': [{'message': 'simulated failure'}]}
    if st.get('fail_page') and _fake_calls[0] >= st['fail_page']: return {'success': False, 'errors': [{'message': f"simulated failure on call {_fake_calls[0]}"}]}
    t0, t1 = body['timeframe']['from'], body['timeframe']['to']
    recs = sorted([r for r in st['records'] if t0 <= r['timestamp'] <= t1 and r['$metadata']['service'] == SERVICE],
                  key=lambda r: (r['timestamp'], r['$metadata']['id']), reverse=True)
    if body.get('offset'):
        ids = [r['$metadata']['id'] for r in recs]
        recs = recs[ids.index(body['offset']) + 1:] if body['offset'] in ids else []
    page = recs[:body['limit']]
    n_all = len([r for r in st['records'] if t0 <= r['timestamp'] <= t1])
    total = n_all + st.get('series_extra', 0)
    series = [] if st.get('drop_series') else [{'time': 't', 'data': [{'count': total, 'sampleInterval': st.get('sample_interval', 1)}]}]
    for r in page:
        if r['$metadata']['id'] in st.get('truncated_ids', []): r['$workers']['truncated'] = True
    return {'success': True, 'result': {'events': {'count': len(page), 'events': page, 'series': series}}}

def query(body):
    return _fake(body) if os.environ.get('OBSQ_FAKE') else _real(body)

def fetch_window(t0, t1):
    out = {'complete': False, 'problems': [], 'pages': 0, 'records': 0, 'series_total': None, 'sample_intervals': [], 'invocations': [], 'invalid': []}
    recs, off, totals, intervals = [], None, set(), set()
    while True:
        b = {'queryId': f'b3-obsq-{out["pages"]}', 'timeframe': {'from': t0, 'to': t1}, 'view': 'events', 'limit': LIMIT, 'parameters': {'filters': [SVC]}}
        if off: b.update(offset=off, offsetDirection='next')
        try: o = query(b)
        except Exception as e: out['problems'].append(f'query error: {str(e)[:200]}'); return out
        out['pages'] += 1
        if not o.get('success'): out['problems'].append(f"success=false on page {out['pages']}: {str(o.get('errors'))[:200]}"); return out
        ev = ((o.get('result') or {}).get('events') or {}).get('events')
        se = ((o.get('result') or {}).get('events') or {}).get('series')
        if ev is None or se is None: out['problems'].append(f"page {out['pages']}: events/series missing"); return out
        totals.add(sum(d.get('count', 0) for s in se for d in s.get('data', [])))
        intervals |= {d.get('sampleInterval') for s in se for d in s.get('data', [])}
        recs += ev
        if len(ev) < LIMIT: break
        off = ev[-1]['$metadata']['id']
        if out['pages'] > 200: out['problems'].append('paging did not reach the end within 200 pages'); return out
    out['records'] = len(recs); out['sample_intervals'] = sorted(intervals, key=str)
    out['series_total'] = sorted(totals)[0] if len(totals) == 1 else sorted(totals)
    if len(totals) != 1: out['problems'].append(f'series total changed between pages: {sorted(totals)}')
    elif recs and not se_ok(len(recs), totals): out['problems'].append(f'records {len(recs)} != series total {sorted(totals)[0]}')
    elif not recs and totals != {0}: out['problems'].append(f'no records but series total {sorted(totals)}')
    if any(i != 1 for i in intervals): out['problems'].append(f'sampled buckets (sampleInterval {sorted(intervals, key=str)})')
    if len({r['$metadata']['id'] for r in recs}) != len(recs): out['problems'].append('duplicate record ids across pages')
    tr = [r['$metadata']['id'] for r in recs if (r.get('$workers') or {}).get('truncated')]
    if tr: out['problems'].append(f'{len(tr)} truncated record(s)')
    other = {r['$metadata'].get('service') for r in recs} - {SERVICE}
    if other: out['problems'].append(f'records from other services {other}')
    by = {}
    for r in recs: by.setdefault(r['$metadata'].get('requestId'), []).append(r)
    for rid, rs in by.items():
        inv = [r for r in rs if r['$metadata'].get('type') == 'cf-worker-event']
        logs = sorted([r for r in rs if r['$metadata'].get('type') == 'cf-worker'], key=lambda r: r['timestamp'])
        oth = [r['$metadata'].get('type') for r in rs if r['$metadata'].get('type') not in ('cf-worker-event', 'cf-worker')]
        if len(inv) != 1 or oth:
            out['invalid'].append({'requestId': rid, 'invocation_records': len(inv), 'other_types': oth}); continue
        c = convert(inv[0], logs)
        if c is None: out['invalid'].append({'requestId': rid, 'reason': 'unconvertible invocation record'})
        else: out['invocations'].append(c)
    if out['invalid']: out['problems'].append(f"{len(out['invalid'])} invalid invocation group(s)")
    out['invocations'].sort(key=lambda x: x['eventTimestamp'])
    out['complete'] = not out['problems']
    return out

def se_ok(n, totals): return totals == {n}

def convert(inv, logs):
    try:
        w = inv['$workers']; ev = w.get('event') or {}; wall = int(w['wallTimeMs'])
        o = {'eventTimestamp': int(inv['timestamp']) - wall, 'wallTime': wall, 'cpuTime': w.get('cpuTimeMs'), 'outcome': w['outcome'],
             'scriptName': w['scriptName'], 'scriptVersion': {'id': w['scriptVersion']['id']}, 'exceptions': [], 'truncated': bool(w.get('truncated')),
             'logs': [{'message': [json.dumps(l['source'])], 'level': (l.get('source') or {}).get('level'), 'timestamp': l['timestamp']} for l in logs],
             '_source': 'workers-logs', '_wl': {'id': inv['$metadata']['id'], 'requestId': inv['$metadata'].get('requestId'), 'end': int(inv['timestamp'])}}
        if w['eventType'] == 'fetch':
            rq = ev['request']
            o['event'] = {'request': {'url': rq['url'], 'method': rq.get('method'), 'headers': rq.get('headers') or {}}, 'response': ev.get('response')}
        elif w['eventType'] == 'scheduled':
            o['event'] = {'cron': ev['cron'], 'scheduledTime': int(ev['scheduledTime']) * 1000}
        else: return None
        return o
    except (KeyError, TypeError, ValueError): return None

def key(o):
    """Correlation key for a tail event or a converted Workers Logs invocation."""
    ev = o.get('event') or {}
    if ev.get('cron'): return ('cron', ev['cron'], int(ev.get('scheduledTime') or 0))
    rq = ev.get('request') or {}
    return ('fetch', (rq.get('headers') or {}).get('cf-ray')) if rq else ('other', o.get('eventTimestamp'))

if __name__ == '__main__':
    r = fetch_window(int(sys.argv[1]), int(sys.argv[2]))
    print(json.dumps({k: v for k, v in r.items() if k != 'invocations'} | {'invocations': len(r['invocations'])}))
    sys.exit(0 if r['complete'] else 2)
