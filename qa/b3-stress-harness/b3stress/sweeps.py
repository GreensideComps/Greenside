#!/usr/bin/env python3
"""Print every cron sweep seen by either tail (or recovered from Workers Logs into obs.jsonl for a tail gap) at/after SINCE_MS (deduplicated by eventTimestamp): version, outcome, exceptions, all log lines.
Usage: sweeps.py DIR SINCE_MS  -> prints JSON lines; exit 0 if at least one sweep with reconcile_summary was found, else 3."""
import json, sys
D, since = sys.argv[1], int(sys.argv[2])
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
        if not isinstance(o, dict) or o.get('eventTimestamp', 0) < since: continue
        ev = o.get('event') or {}
        if not ev.get('cron'): continue
        logs = []
        for l in o.get('logs', []):
            for m in l.get('message', []):
                try: logs.append(json.loads(m))
                except Exception: logs.append(m)
        prev = seen.get(o['eventTimestamp'])
        if prev is None or len(logs) > len(prev['logs']):
            seen[o['eventTimestamp']] = {'ts': o['eventTimestamp'], 'cron': ev.get('cron'), 'scheduledTime': ev.get('scheduledTime'),
                'version': (o.get('scriptVersion') or {}).get('id'), 'outcome': o.get('outcome'), 'exceptions': o.get('exceptions', []), 'logs': logs,
                'source': o.get('_source', 'tail')}
ok = False
for k in sorted(seen):
    print(json.dumps(seen[k]))
    ok = ok or any(isinstance(m, dict) and m.get('event') == 'reconcile_summary' for m in seen[k]['logs'])
sys.exit(0 if ok else 3)
