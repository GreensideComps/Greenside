#!/usr/bin/env python3
"""Tail-continuity proof. Usage: coverage.py DIR FROM_MS TO_MS  -> prints JSON; exit 0 continuous, 2 gap, 3 no probes.
heartbeat.sh sends a tagged /health probe (hb-N) every 4s and logs it. Every probe sent in [FROM_MS, TO_MS] must appear in at
least one of the redundant pre-deploy tails (pre.jsonl, pre2.jsonl), and the largest interval between consecutive covered probes
must be <= 12s. So no Worker invocation in the window can have gone unrecorded for longer than that, whichever tail was reconnecting."""
import json, re, sys, datetime
D, t0, t1 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
day = datetime.datetime.utcfromtimestamp(t1 / 1000).strftime('%Y-%m-%d')
probes = []
for l in open(f'{D}/heartbeat.log'):
    m = re.match(r'(\d\d:\d\d:\d\d) (hb-\d+) ', l)
    if not m: continue
    ts = int(datetime.datetime.strptime(f'{day} {m.group(1)}', '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    if t0 <= ts <= t1: probes.append((ts, m.group(2)))
seen = {'pre.jsonl': set(), 'pre2.jsonl': set()}
for f in seen:
    try: txt = open(f'{D}/{f}', encoding='utf-8', errors='replace').read()
    except FileNotFoundError: continue
    for tag in re.findall(r'probe=(hb-\d+)', txt): seen[f].add(tag)
cov = [(ts, tag) for ts, tag in probes if tag in seen['pre.jsonl'] or tag in seen['pre2.jsonl']]
missing = [tag for ts, tag in probes if (ts, tag) not in cov]
gaps = [(b[0] - a[0]) for a, b in zip(cov, cov[1:])]
maxgap = max(gaps) if gaps else None
edge = [(cov[0][0] - t0) if cov else None, (t1 - cov[-1][0]) if cov else None]
f = lambda ms: datetime.datetime.utcfromtimestamp(ms / 1000).strftime('%H:%M:%S')
ok = bool(probes) and not missing and maxgap is not None and maxgap <= 12000 and edge[0] <= 12000 and edge[1] <= 12000
print(json.dumps({'window': [f(t0), f(t1)], 'probes': len(probes), 'covered': len(cov), 'missing': missing[:20],
                  'by_tail': {k: sum(1 for _, t in probes if t in v) for k, v in seen.items()}, 'max_gap_ms': maxgap, 'edge_gap_ms': edge,
                  'result': 'CONTINUOUS' if ok else ('NO_PROBES' if not probes else 'GAP')}))
sys.exit(0 if ok else (3 if not probes else 2))
