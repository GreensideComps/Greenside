#!/usr/bin/env python3
"""Evidence-loss check for the live window (the guard's tail rule) and the pre-live continuity check.
Usage: evidence.py DIR FROM_MS TO_MS ARMED_MS [EXPECT_DRY_RUN]  -> prints JSON;
exit 0 = INTACT, 2 = EVIDENCE LOST (abort), 3 = window too short to judge yet, 4 = PENDING (a tail gap is waiting for its
Workers Logs recovery; not intact, not lost).
Tails (pre, pre2) are the PRIMARY evidence. The raw checks are unchanged:
  1. coverage.py over [FROM_MS, TO_MS]: every heartbeat probe seen by at least one tail, covered gaps <= 12s, edges <= 12s.
  2. The two tails never unavailable at the same time (restart / stall intervals of pre and pre2 must not overlap).
  3. (new) If EXPECT_DRY_RUN is given, every heartbeat line in the window must report exactly that dry_run value.
Legacy mode (OBS_DISABLE=1): any failure of 1 or 2 = EVIDENCE LOST at once (the approved v2 rule, kept for regression tests).
Default mode: a failure of 1 or 2 defines a TAIL GAP [a, b] (probes missed by both tails, a covered gap > 12s, an edge gap, or both
tails unavailable). Workers Logs (b3obs/obsq.py) is used ONLY for such a gap, never when the tails are continuous:
  - open gap (tails still blind at TO_MS): PENDING; if blind for more than WAIT (60s) -> EVIDENCE LOST.
  - closed gap: nothing is queried before now >= b + WAIT. Then ONE phase-1 attempt over [a-60s, b+12s]:
      the query must be complete (obsq: success, paged to the end, records == series total, every sampleInterval == 1,
      nothing truncated, no invalid invocation group); every heartbeat probe sent in [a, b] must be present in Workers Logs;
      agreement over [a-60s, b+12s] (below). Workers Logs invocations that no tail captured are appended to obs.jsonl, where
      cstate / sweeps / overlap / hooks apply the SAME checks as to tail events (nothing synthetic is ever written).
  - phase 2 at now >= b + 2*WAIT over [b+12s, b+60s]: complete query + agreement; WL-only invocations merged.
  - both phases pass -> RECOVERED. Any failure, or a missed attempt deadline -> EVIDENCE LOST.
Agreement (documented Workers Logs behaviour: isolated single-record loss ~0.47%, tail-union loss ~0.43%, identical fields):
  every tail invocation must have a Workers Logs counterpart (cf-ray for fetch, cron+scheduledTime for crons) with the same
  outcome, script version, wall time, response status and Shopify topic; at most ONE tail invocation per checked window may be
  missing from Workers Logs and it must be isolated (its neighbours matched); at most ONE Workers Logs invocation outside [a, b]
  may be missing from the tails, also isolated. Anything else is unexplained disagreement -> EVIDENCE LOST.
Workers Logs never proves that no other invocation happened; a missing required webhook still fails hooks_done / deadlines."""
import json, os, re, subprocess, sys, time, datetime
D, t0, t1, armed = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
EXPECT = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] in ('true', 'false') else None
LIMIT = 12000                                  # the existing coverage threshold
WAIT = int(os.environ.get('OBS_WAIT_S', '60')) * 1000   # approved hard ingestion wait (tests may shorten it)
LEGACY = os.environ.get('OBS_DISABLE') == '1'
NOW = int(os.environ.get('EVIDENCE_NOW_MS') or time.time() * 1000)
if WAIT < 15000: print(json.dumps({'result': 'ERROR', 'error': 'OBS_WAIT_S below 15s is not allowed'})); sys.exit(5)
sys.path.insert(0, os.environ.get('OBSQ_DIR', '__SCRATCHPAD__/b3obs'))
day = datetime.datetime.utcfromtimestamp(t1 / 1000).strftime('%Y-%m-%d')
hms = lambda s: int(datetime.datetime.strptime(f'{day} {s}', '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
f = lambda ms: datetime.datetime.utcfromtimestamp(ms / 1000).strftime('%H:%M:%S')
if t1 - t0 < LIMIT:
    print(json.dumps({'result': 'WAIT', 'window': [f(t0), f(t1)]})); sys.exit(3)
cov_p = subprocess.run(['python3', f'{D}/coverage.py', D, str(t0), str(t1)], capture_output=True, text=True)
try: cov = json.loads(cov_p.stdout)
except ValueError: cov = {'result': 'ERROR', 'stderr': cov_p.stderr[-300:]}
restarts = []
try:
    for l in open(f'{D}/supervisor.log'):
        m = re.match(r'(\d\d:\d\d:\d\d) RESTART (\w+) \((.*?)\)', l)
        if m and t0 - LIMIT <= hms(m.group(1)) <= t1:
            ts = hms(m.group(1)); restarts.append({'tail': m.group(2), 'at': f(ts), 'ms': ts, 'why': m.group(3), 'during_test': ts >= armed})
except FileNotFoundError: pass

def read_objs(fn, lo, hi):
    out = []
    try: txt = open(f'{D}/{fn}', encoding='utf-8', errors='replace').read()
    except FileNotFoundError: return out
    dec, i = json.JSONDecoder(), 0
    while True:
        j = txt.find('{', i)
        if j < 0: break
        try: o, e = dec.raw_decode(txt, j); i = e
        except ValueError: i = j + 1; continue
        if isinstance(o, dict) and lo <= o.get('eventTimestamp', 0) <= hi: out.append(o)
    return out
cap = {t: sorted(o['eventTimestamp'] for o in read_objs(f'{t}.jsonl', t0 - LIMIT, t1)) for t in ('pre', 'pre2')}
down = {}
for tail in ('pre', 'pre2'):
    c = cap[tail]; iv = []
    for x, y in zip(c, c[1:]):
        if y - x > LIMIT: iv.append([x, y, 'stall'])
    if not c or t1 - c[-1] > LIMIT: iv.append([c[-1] if c else t0 - LIMIT, t1, 'stall (open)'])
    for r in restarts:
        if r['tail'] != tail: continue
        a = max([x for x in c if x <= r['ms'] + 999] or [t0 - LIMIT]); b = min([x for x in c if x > r['ms']] or [t1])
        iv.append([a, b, 'restart'])
    down[tail] = [x for x in iv if x[1] >= t0 and x[0] <= t1]
both = [[max(a[0], b[0]), min(a[1], b[1])] for a in down['pre'] for b in down['pre2'] if a[0] < b[1] and b[0] < a[1]]
# heartbeat probes in the window (sent + answered) and which ones a tail captured
hb = []
for l in open(f'{D}/heartbeat.log'):
    m = re.match(r'(\d\d:\d\d:\d\d) (hb-\d+) dry_run=(\S*)', l)
    if m and t0 <= hms(m.group(1)) <= t1: hb.append((hms(m.group(1)), m.group(2), m.group(3)))
tail_tags = set()
for t in ('pre', 'pre2'):
    try: tail_tags |= set(re.findall(r'probe=(hb-\d+)', open(f'{D}/{t}.jsonl', encoding='utf-8', errors='replace').read()))
    except FileNotFoundError: pass
lost = []
if EXPECT and [x for x in hb if x[2] != EXPECT]:
    lost.append(f"heartbeat not dry_run={EXPECT}: {[(f(x[0]), x[1], x[2]) for x in hb if x[2] != EXPECT][:5]}")
base = {'window': [f(t0), f(t1)], 'coverage': {k: cov.get(k) for k in ('probes', 'covered', 'missing', 'by_tail', 'max_gap_ms', 'edge_gap_ms', 'result')},
        'restarts': [{k: r[k] for k in ('tail', 'at', 'why', 'during_test')} for r in restarts],
        'unavailable': {k: [[f(a), f(b), w] for a, b, w in v] for k, v in down.items()}, 'both_unavailable': [[f(a), f(b)] for a, b in both]}

if LEGACY:
    if cov.get('result') != 'CONTINUOUS': lost.append(f"coverage {cov.get('result')}: missing {cov.get('missing')} max_gap_ms {cov.get('max_gap_ms')} edge_gap_ms {cov.get('edge_gap_ms')}")
    if both: lost.append(f"both tails unavailable at the same time: {[[f(a), f(b)] for a, b in both][:3]}")
    print(json.dumps({'result': 'LOST' if lost else 'INTACT', 'mode': 'legacy', **base, 'lost': lost})); sys.exit(2 if lost else 0)

# ---- raw tail gaps from the probe timeline and the both-unavailable intervals ----
raw = []
if cov.get('result') == 'ERROR': lost.append(f"coverage.py failed: {cov.get('stderr')}")
covd = [x for x in hb if x[1] in tail_tags]
if not hb: raw.append([t0, t1, True])
else:
    prev = None
    for x in hb:
        if x[1] in tail_tags:
            if prev is None:
                if x[0] - t0 > LIMIT or any(y[1] not in tail_tags for y in hb if y[0] < x[0]): raw.append([t0, x[0], False])
            elif x[0] - prev > LIMIT or any(y[1] not in tail_tags for y in hb if prev < y[0] < x[0]): raw.append([prev, x[0], False])
            prev = x[0]
    if prev is None: raw.append([t0, t1, True])
    elif t1 - prev > LIMIT or any(y[1] not in tail_tags for y in hb if y[0] > prev): raw.append([prev, t1, True])
for a, b in both: raw.append([a, b, b >= t1])
raw.sort()
merged = []
for a, b, op in raw:
    if merged and a <= merged[-1][1] + 1000: merged[-1][1] = max(merged[-1][1], b); merged[-1][2] = merged[-1][2] or op
    else: merged.append([a, b, op])

# ---- persistent gap state ----
SF = f'{D}/gaps-{t0}.json'   # one state file per evidence window (pre-live and live windows never share gap state)
try: gaps = json.load(open(SF))
except (FileNotFoundError, ValueError): gaps = []
for a, b, op in merged:
    ov = [g for g in gaps if g['a'] <= b + 1000 and a <= g['b'] + 1000]
    if any(g['a'] <= a and b <= g['b'] and not g['open'] for g in ov if g['status'] != 'LOST') and not op: continue
    if any(g['status'] == 'LOST' for g in ov): continue
    na, nb = min([a] + [g['a'] for g in ov]), max([b] + [g['b'] for g in ov])
    gaps = [g for g in gaps if g not in ov]
    gaps.append({'a': na, 'b': nb, 'open': op, 'status': 'OPEN' if op else 'PENDING', 'detected': NOW, 'notes': []})
gaps.sort(key=lambda g: g['a'])

def tail_invocations(lo, hi):
    T = {}
    import obsq
    for fn in ('pre.jsonl', 'pre2.jsonl', 'conf.jsonl'):
        for o in read_objs(fn, lo, hi): T.setdefault(obsq.key(o), o)
    return T

def agree(T, W, lo, hi, a, b):
    """Documented-behaviour agreement between tail invocations T and Workers Logs invocations W over [lo, hi]."""
    import obsq
    problems = []; wl_only = []
    Ts = sorted([k for k, o in T.items() if lo <= o['eventTimestamp'] <= hi], key=lambda k: T[k]['eventTimestamp'])
    Wk = {k: o for k, o in W.items() if lo <= o['eventTimestamp'] <= hi}
    miss = [i for i, k in enumerate(Ts) if k not in W]
    for k in Ts:
        if k not in W: continue
        t, w = T[k], W[k]; te, we = t.get('event') or {}, w.get('event') or {}
        d = {}
        if t.get('outcome') != w.get('outcome'): d['outcome'] = [t.get('outcome'), w.get('outcome')]
        if (t.get('scriptVersion') or {}).get('id') != (w.get('scriptVersion') or {}).get('id'): d['version'] = 1
        if t.get('wallTime') != w.get('wallTime'): d['wall'] = [t.get('wallTime'), w.get('wallTime')]
        if (te.get('response') or {}).get('status') != (we.get('response') or {}).get('status'): d['status'] = 1
        if ((te.get('request') or {}).get('headers') or {}).get('x-shopify-topic') != ((we.get('request') or {}).get('headers') or {}).get('x-shopify-topic'): d['topic'] = 1
        if d: problems.append(f'tail/Workers Logs field disagreement {k}: {d}')
    if len(miss) > 1: problems.append(f'{len(miss)} tail invocations missing from Workers Logs (documented loss is isolated single records)')
    for i in miss:
        if (i > 0 and Ts[i - 1] not in W) or (i + 1 < len(Ts) and Ts[i + 1] not in W): problems.append('tail invocations missing from Workers Logs are not isolated')
    outside = sorted([k for k in Wk if k not in T and not (a - 3000 <= Wk[k]['eventTimestamp'] <= b + 3000)], key=lambda k: Wk[k]['eventTimestamp'])
    if len(outside) > 1: problems.append(f'{len(outside)} Workers Logs invocations outside the gap missing from the tails (documented tail loss is isolated)')
    allk = sorted(set(Ts) | set(Wk), key=lambda k: (T.get(k) or Wk.get(k))['eventTimestamp'])
    for k in outside:
        i = allk.index(k)
        if (i > 0 and allk[i - 1] not in T) or (i + 1 < len(allk) and allk[i + 1] not in T): problems.append('Workers Logs-only invocation outside the gap is not isolated')
    wl_only = [k for k in Wk if k not in T]
    return problems, wl_only, len(miss)

def merge(W, keys):
    import obsq
    have = {obsq.key(o) for o in read_objs('obs.jsonl', 0, 10 ** 14)}
    add = [W[k] for k in keys if k not in have]
    with open(f'{D}/obs.jsonl', 'a') as fh:
        for o in sorted(add, key=lambda o: o['eventTimestamp']): fh.write(json.dumps(o) + '\n')
    return len(add)

for g in gaps:
    if g['status'] in ('LOST', 'RECOVERED'): continue
    a, b = g['a'], g['b']
    if g['open']:
        if t1 - a > WAIT: g['status'] = 'LOST'; g['notes'].append(f'tails blind from {f(a)} for more than {WAIT // 1000}s (open gap)')
        continue
    if g['status'] in ('OPEN', 'PENDING'):
        if NOW < b + WAIT: g['status'] = 'PENDING'; continue
        if NOW > b + 2 * WAIT: g['status'] = 'LOST'; g['notes'].append('phase-1 attempt deadline missed'); continue
        import obsq
        r = obsq.fetch_window(a - 60000, b + 12000); g['phase1'] = {k: r[k] for k in ('complete', 'problems', 'pages', 'records', 'series_total', 'sample_intervals')}
        if not r['complete']: g['status'] = 'LOST'; g['notes'].append(f"Workers Logs query incomplete: {r['problems']}"); continue
        W = {obsq.key(o): o for o in r['invocations']}
        wl_probes = {}
        for o in r['invocations']:
            m = re.search(r'[?&]probe=(hb-\d+)', ((o.get('event') or {}).get('request') or {}).get('url', ''))
            if m: wl_probes.setdefault(m.group(1), []).append(o['_wl']['end'])
        need = [x for x in hb if a <= x[0] <= b]
        absent = [x[1] for x in need if not any(x[0] - 9000 <= e <= x[0] + 2500 for e in wl_probes.get(x[1], []))]
        if absent: g['status'] = 'LOST'; g['notes'].append(f'heartbeat probes in the gap missing from Workers Logs: {absent}'); continue
        T = tail_invocations(a - 60000, b + 12000)
        pr, wl_only, lostn = agree(T, W, a - 60000, b + 12000, a, b)
        if pr: g['status'] = 'LOST'; g['notes'] += pr; continue
        g['phase1']['probes_proven'] = [x[1] for x in need]; g['phase1']['merged'] = merge(W, wl_only); g['phase1']['wl_isolated_loss'] = lostn
        g['status'] = 'PHASE1_OK'
    if g['status'] == 'PHASE1_OK':
        if NOW < b + 2 * WAIT: continue
        if NOW > b + 3 * WAIT: g['status'] = 'LOST'; g['notes'].append('phase-2 attempt deadline missed'); continue
        import obsq
        r = obsq.fetch_window(b + 12000, b + WAIT); g['phase2'] = {k: r[k] for k in ('complete', 'problems', 'pages', 'records', 'series_total', 'sample_intervals')}
        if not r['complete']: g['status'] = 'LOST'; g['notes'].append(f"Workers Logs query incomplete (phase 2): {r['problems']}"); continue
        W = {obsq.key(o): o for o in r['invocations']}
        T = tail_invocations(b + 12000, b + WAIT)
        pr, wl_only, lostn = agree(T, W, b + 12000, b + WAIT, a, b)
        if pr: g['status'] = 'LOST'; g['notes'] += pr; continue
        g['phase2']['merged'] = merge(W, wl_only); g['phase2']['wl_isolated_loss'] = lostn
        g['status'] = 'RECOVERED'
json.dump(gaps, open(SF, 'w'))
gl = [g for g in gaps if g['status'] == 'LOST']
for g in gl: lost.append(f"tail gap {f(g['a'])}-{f(g['b'])} not recovered: {g['notes'][:3]}")
pend = [g for g in gaps if g['status'] not in ('RECOVERED', 'LOST')]
res = 'LOST' if lost else ('PENDING' if pend else 'INTACT')
out = {'result': res, 'mode': 'workers-logs-recovery', **base, 'wait_s': WAIT // 1000,
       'gaps': [{'from': f(g['a']), 'to': f(g['b']), 'open': g['open'], 'status': g['status'], 'notes': g['notes'][:3],
                 'phase1': g.get('phase1'), 'phase2': g.get('phase2')} for g in gaps], 'lost': lost}
print(json.dumps(out)); sys.exit({'LOST': 2, 'PENDING': 4, 'INTACT': 0}[res])
