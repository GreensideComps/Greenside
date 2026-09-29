#!/usr/bin/env python3
"""Tail-authoritative version check: list every request/event seen by ANY tail at or after SINCE_MS whose
served version is not TARGET. Usage: oldreq.py TARGET SINCE_MS FILE... -> prints count, then details to stderr."""
import json, sys, datetime
target, since, files = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
seen = {}
for path in files:
    try: txt = open(path, encoding='utf-8', errors='replace').read()
    except FileNotFoundError: continue
    dec, i = json.JSONDecoder(), 0
    while True:
        j = txt.find('{', i)
        if j < 0: break
        try: o, e = dec.raw_decode(txt, j); i = e
        except ValueError: i = j + 1; continue
        if not isinstance(o, dict) or o.get('eventTimestamp', 0) < since: continue
        v = (o.get('scriptVersion') or {}).get('id') or ''
        if v and v != target:
            ev = o.get('event') or {}; u = ((ev.get('request') or {}).get('url') or ('cron' if ev.get('cron') else '-'))
            seen[(o['eventTimestamp'], u)] = v
print(len(seen))
for (ts, u), v in sorted(seen.items()):
    print(f"  old-version request {datetime.datetime.utcfromtimestamp(ts/1000).strftime('%H:%M:%S.%f')[:-3]} v={v[:8]} {u.split('.dev',1)[-1]}", file=sys.stderr)
