#!/usr/bin/env python3
"""Robust reader for wrangler tail --format json files (tolerates restarts/junk lines)."""
import json, sys
def events(path):
    try: txt = open(path, encoding='utf-8', errors='replace').read()
    except FileNotFoundError: return []
    dec, i, out = json.JSONDecoder(), 0, []
    while True:
        j = txt.find('{', i)
        if j < 0: break
        try:
            obj, end = dec.raw_decode(txt, j)
            if isinstance(obj, dict) and 'eventTimestamp' in obj: out.append(obj)
            i = end
        except ValueError:
            i = j + 1
    return out
def url(e): return ((e.get('event') or {}).get('request') or {}).get('url') or ''
def ver(e): return ((e.get('scriptVersion') or {}).get('id')) or ''
mode, path, *args = sys.argv[1:]
ev = events(path)
if mode == 'count': print(len(ev))
elif mode == 'probe':          # probe FILE TAG -> version that served ?probe=TAG (empty if not seen)
    hits = [ver(e) for e in ev if f'probe={args[0]}' in url(e)]
    print(hits[0] if hits else '')
elif mode == 'versions':       # versions FILE TAG... -> JSON {tag: version or null}
    print(json.dumps({t: next((ver(e) for e in ev if f'probe={t}' in url(e)), None) for t in args}))
elif mode == 'lines':          # human summary
    for e in sorted(ev, key=lambda e: e['eventTimestamp']):
        r = (e.get('event') or {}); req = r.get('request') or {}; logs = [m for l in e.get('logs', []) for m in l.get('message', [])]
        import datetime
        t = datetime.datetime.utcfromtimestamp(e['eventTimestamp']/1000).strftime('%H:%M:%S')
        u = url(e).split('.dev', 1)[-1] if url(e) else ('cron' if r.get('cron') else '-')
        print(f"{t} {req.get('method','')} {u} -> {(r.get('response') or {}).get('status','-')} v={ver(e)[:8]} outcome={e.get('outcome')} exc={len(e.get('exceptions',[]))} logs={logs}")
