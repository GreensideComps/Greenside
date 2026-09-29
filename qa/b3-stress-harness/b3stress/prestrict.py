#!/usr/bin/env python3
"""Strict pre-live rule: the most recent trailing (*/15) AND deep (20,50) dry-run sweeps captured since SINCE_MS must both be
all zeros on DRYVER. Usage: prestrict.py DIR SINCE_MS DRYVER -> exit 0 PASS, 3 not yet (one of them missing), 2 FAIL."""
import json, subprocess, sys
D, since, ver = sys.argv[1], sys.argv[2], sys.argv[3]
sw = [json.loads(l) for l in subprocess.run(['python3', f'{D}/sweeps.py', D, since], capture_output=True, text=True).stdout.splitlines() if l.strip()]
sw = [s for s in sw if any(isinstance(m, dict) and m.get('event') == 'reconcile_summary' for m in s['logs'])]
last = {c: next((s for s in reversed(sw) if s['cron'] == c), None) for c in ('*/15 * * * *', '20,50 * * * *')}
if None in last.values(): print(json.dumps({'result': 'WAIT', 'have': [c for c, s in last.items() if s]})); sys.exit(3)
dev = []; rep = []
for c, s in last.items():
    su = next(m for m in s['logs'] if isinstance(m, dict) and m.get('event') == 'reconcile_summary')
    other = [m for m in s['logs'] if not isinstance(m, dict) or m.get('event') not in ('reconcile_started', 'reconcile_summary')]
    exp = {'dry_run': True, 'report': True, 'mismatched': 0, 'converged_orders': 0, 'claimed': 0, 'released': 0, 'refused_not_open': 0, 'unreadable': 0, 'errors': 0, 'truncated': False, 'aged_held': 0}
    bad = {k: su.get(k) for k, v in exp.items() if su.get(k) != v}
    if s['version'] != ver: bad['version'] = s['version']
    if s['outcome'] != 'ok' or s['exceptions']: bad['outcome'] = s['outcome']
    if other: bad['other_logs'] = other
    rep.append({'cron': c, 'run_id': su.get('run_id'), 'mode': su.get('mode'), 'orders_seen': su.get('orders_seen'), 'in_scope': su.get('in_scope'), 'mismatched': su.get('mismatched'), 'errors': su.get('errors'), 'bad': bad})
    if bad: dev.append(c)
print(json.dumps({'result': 'FAIL' if dev else 'PASS', 'sweeps': rep})); sys.exit(2 if dev else 0)
