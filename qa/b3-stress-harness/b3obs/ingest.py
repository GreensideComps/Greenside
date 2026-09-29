#!/usr/bin/env python3
"""VALIDATION TOOL ONLY. Ingestion-delay measurement: every POLL seconds, read-only query of Workers Logs invocation records in
[START, now]; the first time each record ($metadata.id) is seen, log {id, kind, probe/cron, wl_ts, wall, seen_ms, prev_poll_ms}.
Delay (upper bound) = seen_ms - wl_ts; lower bound = prev_poll_ms - wl_ts. Usage: ingest.py OUTDIR START_MS DURATION_S POLL_S"""
import json, sys, time
sys.path.insert(0, sys.path[0]); from wlfetch import query, SVC
OUT, START, DUR, POLL = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
TYPE = {'key': '$metadata.type', 'operation': 'eq', 'type': 'string', 'value': 'cf-worker-event'}
seen = set(); prev = None; end = time.time() + DUR; n = 0
log = open(f'{OUT}/ingest.jsonl', 'a'); polls = open(f'{OUT}/polls.jsonl', 'a')
while time.time() < end:
    q0 = int(time.time() * 1000); n += 1
    try:
        o = query({'queryId': f'b3-ingest-{n}', 'timeframe': {'from': START, 'to': q0}, 'view': 'events', 'limit': 2000, 'parameters': {'filters': [SVC, TYPE]}})
        ev = o['result']['events']['events']; q1 = int(time.time() * 1000)
        si = sorted({d.get('sampleInterval') for s in o['result']['events']['series'] for d in s['data']})
        polls.write(json.dumps({'n': n, 'q0': q0, 'q1': q1, 'returned': len(ev), 'sampleIntervals': si, 'full_page': len(ev) >= 2000}) + '\n'); polls.flush()
        for e in ev:
            i = e['$metadata']['id']
            if i in seen: continue
            seen.add(i); w = e['$workers']; x = w.get('event', {})
            log.write(json.dumps({'id': i, 'kind': w['eventType'], 'tag': ((x.get('search') or {}).get('probe')) or x.get('cron') or (x.get('request') or {}).get('url', '')[-40:],
                                  'wl_ts': e['timestamp'], 'wall': w['wallTimeMs'], 'seen_ms': q1, 'prev_poll_ms': prev, 'ray': ((x.get('request') or {}).get('headers') or {}).get('cf-ray'),
                                  'sched': x.get('scheduledTime')}) + '\n'); log.flush()
        prev = q1
    except Exception as ex:
        polls.write(json.dumps({'n': n, 'q0': q0, 'error': str(ex)[:300]}) + '\n'); polls.flush()
    time.sleep(max(0, POLL - (time.time() - q0 / 1000)))
