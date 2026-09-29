#!/usr/bin/env python3
"""Offline + read-only tests for the Workers Logs gap-recovery layer (obsq.py, evidence.py, cstate/overlap merge).
Usage: wltest.py N_DIR OBS_DIR WORK_DIR REPLAY_DIR [--no-real]"""
import json, os, shutil, subprocess, sys, datetime, copy
N, OBS, W, REPLAY = sys.argv[1:5]; REAL = '--no-real' not in sys.argv
os.makedirs(W, exist_ok=True)
sys.path.insert(0, OBS); import obsq
res = []
def chk(name, ok, info=''): res.append(bool(ok)); print(f"{'PASS' if ok else 'FAIL'} {name}  {str(info)[:260]}", flush=True)
SVC = 'greenside-entry-allocator-qa'; V = 'ffff0000-live'; OLD = 'eeee0000-old'
BASE = int(datetime.datetime(2026, 9, 29, 10, 0, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)
hms = lambda ms: datetime.datetime.utcfromtimestamp(ms / 1000).strftime('%H:%M:%S')
_id = [0]
def nid(): _id[0] += 1; return f'01TEST{_id[0]:020d}'

# ---------- builders (tail shape and Workers Logs shape of the same invocation) ----------
def inv(start, kind, tag=None, ver=V, status=200, outcome='ok', wall=1, topic=None, og=None, logs=None, cron=None):
    ray = f'ray{start}{tag or cron or topic}'.replace(' ', '')
    if kind == 'health':
        url = f'https://x.workers.dev/health?probe={tag}'; hdr = {'cf-ray': ray}
    elif kind == 'hook':
        url = f'https://x.workers.dev/webhooks/{topic}'
        hdr = {'cf-ray': ray, 'x-shopify-topic': topic, 'x-shopify-hmac-sha256': 'h', 'x-shopify-shop-domain': '7r5csb-1j.myshopify.com',
               'x-shopify-api-version': '2026-07', 'x-shopify-webhook-id': f'w{start}', 'x-shopify-triggered-at': 't'}
        logs = logs if logs is not None else [{'level': 'info', 'event': 'webhook_processed', 'topic': topic, 'order_gid': og, 'lines': 1}]
        wall = 700 if wall == 1 else wall
    logs = logs or []
    if kind == 'cron':
        tail = {'eventTimestamp': start, 'wallTime': wall, 'outcome': outcome, 'scriptVersion': {'id': ver}, 'exceptions': [], 'logs': [{'message': [json.dumps(l)]} for l in logs],
                'event': {'cron': cron, 'scheduledTime': (start // 1000 - 15) * 1000}}
    else:
        tail = {'eventTimestamp': start, 'wallTime': wall, 'outcome': outcome, 'scriptVersion': {'id': ver}, 'exceptions': [], 'logs': [{'message': [json.dumps(l)]} for l in logs],
                'event': {'request': {'url': url, 'headers': hdr}, 'response': {'status': status}}}
    rid = f'rid{start}{tag or topic or cron}'
    ev = {'cron': cron, 'scheduledTime': start // 1000 - 15} if kind == 'cron' else \
         {'request': {'url': url, 'method': 'GET' if kind == 'health' else 'POST', 'headers': hdr}, 'response': {'status': status}, **({'search': {'probe': tag}} if tag else {})}
    wl = [{'timestamp': start + wall, 'dataset': 'cloudflare-workers',
           '$metadata': {'id': nid(), 'requestId': rid, 'service': SVC, 'type': 'cf-worker-event'},
           '$workers': {'eventType': 'scheduled' if kind == 'cron' else 'fetch', 'scriptName': SVC, 'scriptVersion': {'id': ver}, 'outcome': outcome,
                        'wallTimeMs': wall, 'cpuTimeMs': 0, 'truncated': False, 'event': ev}}]
    for i, l in enumerate(logs):
        wl.append({'timestamp': start + i + 1, 'dataset': 'cloudflare-workers', 'source': l,
                   '$metadata': {'id': nid(), 'requestId': rid, 'service': SVC, 'type': 'cf-worker'}, '$workers': {'scriptName': SVC, 'truncated': False}})
    return tail, wl

class Scene:
    """Synthetic 300s evidence window: heartbeat probe every 4s (hb-1..hb-75), both tails capture every probe except the gap."""
    def __init__(s, name, gap=(30, 32), wl_skip=(), tail_extra=(), wl_extra=(), expect='false', hbval='false', both_skip=()):
        s.d = f'{W}/{name}'; shutil.rmtree(s.d, ignore_errors=True); os.makedirs(s.d)
        for fn in ('evidence.py', 'coverage.py', 'cstate.py', 'sweeps.py', 'overlap.py', 'stress.json'): shutil.copy(f'{N}/{fn}', s.d)
        open(f'{s.d}/supervisor.log', 'w').close(); s.expect = expect
        hb, pre, wl = [], [], []
        s.probe_ts = {}
        for n in range(1, 76):
            sec = BASE + 4000 * (n - 1); start = sec - 300; tag = f'hb-{n}'; s.probe_ts[tag] = start
            hb.append(f'{hms(sec)} {tag} dry_run={hbval}')
            t, w = inv(start, 'health', tag)
            if not (gap and gap[0] <= n <= gap[1]) and n not in both_skip: pre.append(t)
            if n not in wl_skip: wl += w
        for t, w in tail_extra: pre.append(t); wl += w
        for w in wl_extra: wl += w
        open(f'{s.d}/heartbeat.log', 'w').write('\n'.join(hb) + '\n')
        for fn in ('pre.jsonl', 'pre2.jsonl'): open(f'{s.d}/{fn}', 'w').write('\n'.join(json.dumps(o) for o in sorted(pre, key=lambda o: o['eventTimestamp'])) + '\n')
        s.store = {'records': wl}; s.flush()
        s.t0, s.t1 = BASE - 2000, BASE + 300000
        s.a = BASE + 4000 * (gap[0] - 2) if gap else None; s.b = BASE + 4000 * gap[1] if gap else None
    def flush(s): json.dump(s.store, open(f'{s.d}/wlstore.json', 'w'))
    def ev(s, now, env=None, legacy=False):
        e = dict(os.environ, OBSQ_FAKE=f'{s.d}/wlstore.json', OBSQ_DIR=OBS, EVIDENCE_NOW_MS=str(now), OBSQ_CALLLOG=f'{s.d}/calls.log', **(env or {}))
        e.pop('OBS_WAIT_S', None)
        if legacy: e['OBS_DISABLE'] = '1'
        else: e.pop('OBS_DISABLE', None)
        r = subprocess.run(['python3', f'{s.d}/evidence.py', s.d, str(s.t0), str(s.t1), str(BASE), s.expect], capture_output=True, text=True, env=e)
        try: return r.returncode, json.loads(r.stdout)
        except ValueError: return r.returncode, {'stdout': r.stdout, 'stderr': r.stderr[-500:]}
    def calls(s):
        try: return len(open(f'{s.d}/calls.log').read().splitlines())
        except FileNotFoundError: return 0
    def run3(s):
        r1 = s.ev(s.b + 30000); r2 = s.ev(s.b + 61000); r3 = s.ev(s.b + 121000); return r1, r2, r3
    def cstate(s, bound, armed=BASE):
        open(f'{s.d}/qag-start', 'w').write(str(armed)); open(f'{s.d}/bound-orders.txt', 'w').write('\n'.join(bound) + '\n')
        return json.loads(subprocess.run(['python3', f'{s.d}/cstate.py', s.d, '0', V], capture_output=True, text=True).stdout)

# the fake transport must record calls: patch obsq._fake via a sitecustomize-free route (call log written by obsq when OBSQ_CALLLOG set)
src = open(f'{OBS}/obsq.py').read()
assert 'OBSQ_CALLLOG' in src, 'obsq.py must log fake calls when OBSQ_CALLLOG is set'
G = lambda o: [g for g in o.get('gaps', [])]

print('--- A. obsq pagination / completeness (simulated store) ---')
def store(recs, **kw):
    p = f'{W}/store.json'; json.dump({'records': recs, **kw}, open(p, 'w')); os.environ['OBSQ_FAKE'] = p; os.environ['OBSQ_CALLLOG'] = f'{W}/store-calls.log'
    try: os.remove(f'{W}/store-calls.log')
    except FileNotFoundError: pass
    obsq._fake_calls[0] = 0
many = []
for i in range(4500): many += inv(BASE + i * 10, 'health', f'p-{i}')[1]
store(many); r = obsq.fetch_window(BASE - 1000, BASE + 100000)
bodies = [json.loads(l) for l in open(f'{W}/store-calls.log')]
chk('A1 4500 records -> 3 pages of <=2000, complete, all 4500 invocations', r['complete'] and r['pages'] == 3 and len(r['invocations']) == 4500, {k: r[k] for k in ('pages', 'records', 'series_total', 'problems')})
chk('A2 paging uses limit 2000 + offset=<last $metadata.id> + offsetDirection=next', obsq.LIMIT == 2000 and all(b['limit'] == 2000 for b in bodies) and 'offset' not in bodies[0]
    and bodies[1].get('offsetDirection') == 'next' and bodies[1]['offset'] == sorted(many, key=lambda x: (x['timestamp'], x['$metadata']['id']), reverse=True)[1999]['$metadata']['id'], [ {k: b.get(k) for k in ('limit', 'offsetDirection')} for b in bodies])
exact = []
for i in range(2000): exact += inv(BASE + i * 10, 'health', f'e-{i}')[1]
store(exact); r = obsq.fetch_window(BASE - 1000, BASE + 100000)
chk('A3 exactly 2000 records: a full page is NOT assumed complete; paging continues to the empty page', r['complete'] and r['pages'] == 2 and r['records'] == 2000, {k: r[k] for k in ('pages', 'records')})
small = []
for i in range(60): small += inv(BASE + i * 10, 'health', f's-{i}')[1]
store(small, success_false=True); r = obsq.fetch_window(BASE - 1000, BASE + 100000); chk('A4 success=false -> incomplete', not r['complete'], r['problems'])
store(many, fail_page=2); r = obsq.fetch_window(BASE - 1000, BASE + 100000); chk('A5 paging failure on page 2 -> incomplete', not r['complete'] and r['pages'] == 2, r['problems'])
store(small, series_extra=1); r = obsq.fetch_window(BASE - 1000, BASE + 100000); chk('A6 records != series total -> incomplete', not r['complete'], r['problems'])
store(small, sample_interval=10); r = obsq.fetch_window(BASE - 1000, BASE + 100000); chk('A7 sampleInterval > 1 -> incomplete', not r['complete'], r['problems'])
store(small, truncated_ids=[small[5]['$metadata']['id']]); r = obsq.fetch_window(BASE - 1000, BASE + 100000); chk('A8 $workers.truncated=true -> incomplete', not r['complete'], r['problems'])
store(small, drop_series=True); r = obsq.fetch_window(BASE - 1000, BASE + 100000); chk('A9 series missing -> incomplete', not r['complete'], r['problems'])
bad = copy.deepcopy(small); bad.append({**copy.deepcopy(small[0]), '$metadata': {**small[0]['$metadata'], 'id': nid(), 'type': 'cf-worker-exception'}})
store(bad); r = obsq.fetch_window(BASE - 1000, BASE + 100000); chk('A10 unknown record type in an invocation group -> incomplete (invalid)', not r['complete'] and r['invalid'], r['problems'])
dup = copy.deepcopy(small); dup.append({**copy.deepcopy(small[0]), '$metadata': {**small[0]['$metadata'], 'id': nid()}})
store(dup); r = obsq.fetch_window(BASE - 1000, BASE + 100000); chk('A11 two invocation records for one requestId -> incomplete', not r['complete'], r['problems'])
t, w = inv(BASE + 5000, 'hook', topic='orders/create', og='gid://shopify/Order/1'); tc, wc = inv(BASE + 9000, 'cron', cron='*/15 * * * *', logs=[{'level': 'info', 'event': 'reconcile_summary', 'dry_run': False}], wall=1200)
store(w + wc); r = obsq.fetch_window(BASE - 1000, BASE + 100000); h = [x for x in r['invocations'] if x['event'].get('request')][0]; c = [x for x in r['invocations'] if x['event'].get('cron')][0]
chk('A12 conversion: START = timestamp - wallTimeMs, END kept, headers + logs (by requestId) preserved, cron scheduledTime s->ms',
    r['complete'] and h['eventTimestamp'] == t['eventTimestamp'] and h['_wl']['end'] == t['eventTimestamp'] + 700 and h['event']['request']['headers'] == t['event']['request']['headers']
    and h['logs'] == [{'message': t['logs'][0]['message'], 'level': 'info', 'timestamp': w[1]['timestamp']}] and c['event']['scheduledTime'] == tc['event']['scheduledTime'] and obsq.key(h) == obsq.key(t) and obsq.key(c) == obsq.key(tc), '')
os.environ.pop('OBSQ_FAKE', None); os.environ.pop('OBSQ_CALLLOG', None)
if REAL:
    d28 = lambda s: int(datetime.datetime.strptime(f'2026-09-28 {s}', '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    r = obsq.fetch_window(d28('15:01:00'), d28('15:16:30'))
    chk('A13 REAL read-only: 28 Sep 16:01-16:16:30 BST -> complete, 220 records, 218 invocations (as validated)', r['complete'] and r['records'] == 220 and len(r['invocations']) == 218, {k: r[k] for k in ('pages', 'records', 'series_total', 'problems')})
    r = obsq.fetch_window(d28('08:27:00'), d28('11:17:30'))
    chk('A14 REAL read-only: 28 Sep 09:27-12:17 BST (>2000 records) -> multi-page, complete, records == series total', r['complete'] and r['pages'] >= 2 and r['records'] == r['series_total'] and r['records'] > 2000, {k: r[k] for k in ('pages', 'records', 'series_total', 'problems')})

print('--- B. synthetic gap recovery (evidence.py + simulated Workers Logs) ---')
s = Scene('b1'); (c1, o1), (c2, o2), (c3, o3) = s.run3()
obs = [json.loads(l) for l in open(f'{s.d}/obs.jsonl')] if os.path.exists(f'{s.d}/obs.jsonl') else []
chk('B1 complete gap recovered: PENDING before b+60 (no query), PHASE1 at b+60, RECOVERED/INTACT at b+120; hb-29..33 (gap + both boundary probes) proven in Workers Logs, only the tail-missed hb-30..32 merged',
    c1 == 4 and s.calls() == 2 and c2 == 4 and G(o2)[0]['status'] == 'PHASE1_OK' and c3 == 0 and o3['result'] == 'INTACT' and G(o3)[0]['status'] == 'RECOVERED'
    and G(o3)[0]['phase1']['probes_proven'] == ['hb-29', 'hb-30', 'hb-31', 'hb-32', 'hb-33'] and sorted(x['event']['request']['url'][-5:] for x in obs) == ['hb-30', 'hb-31', 'hb-32'] and all(x['_source'] == 'workers-logs' for x in obs),
    [o1.get('result'), o2.get('result'), o3.get('result'), G(o3)[:1]])
s = Scene('b1q'); c, o = s.ev(s.b + 59000); chk('B2 no query before now >= b + 60s (PENDING, 0 Workers Logs calls)', c == 4 and s.calls() == 0 and G(o)[0]['status'] == 'PENDING', G(o))
s = Scene('b2'); s.store['series_extra'] = 1; s.flush(); c, o = s.ev(s.b + 61000); chk('B3 incomplete query (records != series) -> EVIDENCE LOST', c == 2 and 'incomplete' in json.dumps(o['lost']), o['lost'])
s = Scene('b3', wl_skip=(31,)); c, o = s.ev(s.b + 61000); chk('B4 heartbeat in the gap missing from Workers Logs -> EVIDENCE LOST', c == 2 and 'hb-31' in json.dumps(o['lost']), o['lost'])
s = Scene('b4'); s.store['truncated_ids'] = [s.store['records'][25]['$metadata']['id']]; s.flush(); c, o = s.ev(s.b + 61000); chk('B5 truncated record -> EVIDENCE LOST', c == 2 and 'truncated' in json.dumps(o['lost']), o['lost'])
s = Scene('b5'); s.store['sample_interval'] = 1.6; s.flush(); c, o = s.ev(s.b + 61000); chk('B6 sampleInterval > 1 -> EVIDENCE LOST', c == 2 and 'sampled' in json.dumps(o['lost']), o['lost'])
s = Scene('b6'); s.store['fail_page'] = 1; s.flush(); c, o = s.ev(s.b + 61000); chk('B7 query/paging failure -> EVIDENCE LOST', c == 2 and 'success=false' in json.dumps(o['lost']), o['lost'])
s = Scene('b7', gap=(70, 75)); s.t1 = BASE + 4000 * 74 + 5000; c, o = s.ev(s.t1 + 15000)
chk('B8a open gap (tails blind to the end of the window) within 60s -> PENDING, no query', c == 4 and s.calls() == 0 and G(o)[0]['open'], G(o))
s = Scene('b7b', gap=(55, 75)); s.t1 = BASE + 4000 * 74 + 5000; c, o = s.ev(s.t1 + 15000)
chk('B8b tails blind for more than 60s (open gap) -> EVIDENCE LOST', c == 2 and 'more than 60s' in json.dumps(o['lost']), o['lost'])
s = Scene('b7c'); c, o = s.ev(s.b + 125000); chk('B8c phase-1 not attempted before b + 120s (deadline missed) -> EVIDENCE LOST', c == 2 and 'deadline' in json.dumps(o['lost']), o['lost'])
s = Scene('b7d'); s.ev(s.b + 61000); c, o = s.ev(s.b + 185000); chk('B8d phase-2 not attempted before b + 180s -> EVIDENCE LOST', c == 2 and 'deadline' in json.dumps(o['lost']), o['lost'])
s = Scene('b7e'); c, o = s.ev(s.b + 30000); chk('B8e default hard wait is 60s when OBS_WAIT_S is not set', o.get('wait_s') == 60, o.get('wait_s'))
OG = ['gid://shopify/Order/1', 'gid://shopify/Order/2']
def hookscene(name, **hk):
    ts = BASE + 4000 * 29 + 1000
    return Scene(name, wl_extra=[inv(ts, 'hook', topic=hk.pop('topic', 'orders/create'), og=hk.pop('og', OG[0]), **hk)[1]])
s = hookscene('b8', ver=OLD); s.run3(); cs = s.cstate(OG)
chk('B9 recovered webhook on a bad version -> merged, and cstate flags it (same checks as tail events)', any('version' in d for d in cs['deviations']) and cs['hooks'][0]['source'] == 'workers-logs', cs['deviations'][:1])
s = hookscene('b9', status=500); s.run3(); cs = s.cstate(OG); chk('B10 recovered webhook status 500 -> cstate deviation', any('status 500' in d for d in cs['deviations']), cs['deviations'][:1])
s = hookscene('b9b', outcome='exception'); s.run3(); cs = s.cstate(OG); chk('B11 recovered webhook outcome exception -> cstate deviation', any('outcome exception' in d for d in cs['deviations']), cs['deviations'][:1])
s = hookscene('b10', topic='refunds/create'); s.run3(); cs = s.cstate(OG); chk('B12 unexpected webhook topic recovered -> cstate deviation', any('unexpected topic' in d for d in cs['deviations']), cs['deviations'][:1])
s = hookscene('b10b', og='gid://shopify/Order/99'); s.run3(); cs = s.cstate(OG); chk('B13 recovered webhook for an unbound order -> cstate deviation', any('not one of the bound' in d for d in cs['deviations']), cs['deviations'][:1])
s = hookscene('b11'); s.run3(); cs = s.cstate(OG)
chk('B14 lifecycle: only orders/create recovered (paid never seen anywhere) -> hooks_done FALSE (deadline stays fail-closed); no synthetic paid', cs['hooks_done'] is False and not cs['deviations'] and cs['per_topic'] == {f'{OG[0]}|orders/create': 1}, cs['per_topic'])
s = hookscene('b11b', logs=[]); s.run3(); cs = s.cstate(OG); chk('B15 recovered webhook whose log line Workers Logs lost -> cstate deviation (no webhook_processed)', any('logs' in d for d in cs['deviations']), cs['deviations'][:1])
s = Scene('b12', wl_skip=(20,)); r1, r2, r3 = s.run3()
chk('B16 Workers Logs isolated single-record loss outside the gap (documented) -> tolerated, INTACT, counted', r3[0] == 0 and G(r3[1])[0]['phase1']['wl_isolated_loss'] == 1, G(r3[1])[:1])
s = Scene('b13', wl_skip=(20, 21)); c, o = s.ev(s.b + 61000); chk('B17 two consecutive tail invocations missing from Workers Logs -> unexplained -> EVIDENCE LOST', c == 2 and 'missing from Workers Logs' in json.dumps(o['lost']), o['lost'])
s = Scene('b13b', wl_skip=(16, 22)); c, o = s.ev(s.b + 61000); chk('B18 two (non-consecutive) tail invocations missing from Workers Logs in one window -> EVIDENCE LOST', c == 2, o['lost'])
s = Scene('b14'); [r for r in s.store['records'] if r['$metadata']['type'] == 'cf-worker-event' and r['$workers']['event'].get('search', {}).get('probe') == 'hb-20'][0]['$workers']['event']['response']['status'] = 503; s.flush()
c, o = s.ev(s.b + 61000); chk('B19 tail/Workers Logs field disagreement (status) -> EVIDENCE LOST', c == 2 and 'disagreement' in json.dumps(o['lost']), o['lost'])
s = Scene('b14b'); [r for r in s.store['records'] if r['$metadata']['type'] == 'cf-worker-event' and r['$workers']['event'].get('search', {}).get('probe') == 'hb-21'][0]['$workers']['scriptVersion']['id'] = OLD; s.flush()
c, o = s.ev(s.b + 61000); chk('B20 tail/Workers Logs version disagreement -> EVIDENCE LOST', c == 2 and 'disagreement' in json.dumps(o['lost']), o['lost'])
x1 = inv(BASE + 4000 * 15 + 1500, 'health', 'extra-1')[1]; x2 = inv(BASE + 4000 * 16 + 1500, 'health', 'extra-2')[1]
s = Scene('b15', wl_extra=[x1, x2]); c, o = s.ev(s.b + 61000); chk('B21 two Workers Logs-only invocations outside the gap (tails missed them) -> EVIDENCE LOST', c == 2 and 'missing from the tails' in json.dumps(o['lost']), o['lost'])
s = Scene('b15b', wl_extra=[x1]); r1, r2, r3 = s.run3(); obs = [json.loads(l) for l in open(f'{s.d}/obs.jsonl')]
chk('B22 one isolated Workers Logs-only invocation outside the gap -> tolerated and merged as evidence', r3[0] == 0 and any('extra-1' in o['event']['request']['url'] for o in obs), len(obs))
s = Scene('b16', gap=None); c, o = s.ev(BASE + 400000); chk('B23 tails continuous -> Workers Logs never queried, INTACT', c == 0 and s.calls() == 0 and not G(o), o.get('result'))
s = Scene('b17', gap=None, hbval='true'); c, o = s.ev(BASE + 400000); chk('B24 heartbeat not dry_run=false in the live window -> EVIDENCE LOST', c == 2 and 'heartbeat not dry_run=false' in json.dumps(o['lost']), o['lost'])
s = Scene('b18'); c, o = s.ev(s.b + 61000, legacy=True); chk('B25 legacy mode (OBS_DISABLE=1): the same gap -> EVIDENCE LOST at once, no query', c == 2 and s.calls() == 0 and o.get('mode') == 'legacy', o.get('lost'))
th, wh = inv(BASE + 4000 * 40 + 1000, 'hook', topic='orders/create', og='gid://shopify/Order/2')
s = Scene('b19', tail_extra=[(th, wh)], wl_extra=[inv(BASE + 4000 * 29 + 1000, 'hook', topic='orders/create', og='gid://shopify/Order/1')[1]]); s.run3()
ov = json.loads(subprocess.run(['python3', f'{s.d}/overlap.py', s.d, str(BASE), str(BASE + 400000)], capture_output=True, text=True).stdout)
chk('B26 overlap.py reads the merged evidence: tail webhook + Workers Logs-recovered webhook = 2 invocations (tail copy in pre and pre2 not double counted)', len(ov['invocations']) == 2 and sorted(i['kind'] for i in ov['invocations']) == ['hook', 'hook'], ov['invocations'])
cs_dup = Scene('b20', gap=None); t, w = inv(BASE + 4000 * 29 + 1000, 'hook', topic='orders/create', og=OG[0])
for fn in ('pre.jsonl', 'obs.jsonl'):
    with open(f'{cs_dup.d}/{fn}', 'a') as fh: fh.write(json.dumps(t if fn == 'pre.jsonl' else obsq.convert(w[0], w[1:])) + '\n')
cs = cs_dup.cstate(OG); chk('B27 cstate de-duplicates a tail event and its Workers Logs copy by cf-ray', len(cs['hooks']) == 1, len(cs['hooks']))

print('--- C. real historical replay: 28 Sep 16:13:30-16:14:15 BST blind spot (real Workers Logs, read-only) ---')
if REAL:
    R = f'{W}/replay'; shutil.rmtree(R, ignore_errors=True); shutil.copytree(REPLAY, R)
    for fn in ('evidence.py', 'coverage.py'): shutil.copy(f'{N}/{fn}', R)
    for p in os.listdir(R):
        if p.startswith('gaps-') or p == 'obs.jsonl': os.remove(f'{R}/{p}')
    TG = int(open(f'{R}/tgate.txt').read()); AR = int(open(f'{R}/qag-start').read())
    T1 = int(datetime.datetime(2026, 9, 28, 15, 14, 15, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    # 16:02:12-16:14:15 BST spans the live gate and the restore (heartbeat false then true), so no dry_run expectation is passed here
    def rev2(now):
        e = dict(os.environ, OBSQ_DIR=OBS, EVIDENCE_NOW_MS=str(now)); e.pop('OBSQ_FAKE', None); e.pop('OBS_DISABLE', None); e.pop('OBS_WAIT_S', None)
        r = subprocess.run(['python3', f'{R}/evidence.py', R, str(TG), str(T1), str(AR)], capture_output=True, text=True, env=e)
        return r.returncode, json.loads(r.stdout)
    c1, o1 = rev2(T1 + 15000); st = json.load(open(f'{R}/gaps-{TG}.json')); b = max(g['b'] for g in st)
    c2, o2 = rev2(b + 61000); c3, o3 = rev2(b + 121000)
    obs = [json.loads(l) for l in open(f'{R}/obs.jsonl')] if os.path.exists(f'{R}/obs.jsonl') else []
    got = sorted(x['event']['request']['url'].split('probe=')[-1] for x in obs if 'probe=' in x['event'].get('request', {}).get('url', ''))
    g3 = G(o3)
    chk('C1 blind spot detected as a tail gap, PENDING (no premature query)', c1 == 4 and G(o1) and all(g['status'] in ('PENDING', 'OPEN') for g in G(o1)), G(o1))
    chk('C2 phase 1 at b+60s: complete query, every gap heartbeat present in Workers Logs', c2 == 4 and all(g['status'] == 'PHASE1_OK' for g in G(o2)), [(g['from'], g['to'], g['status'], (g.get('phase1') or {}).get('probes_proven'), g['notes']) for g in G(o2)])
    chk('C3 hb-699, hb-700, hb-701 recovered from Workers Logs and merged (validated shape, source workers-logs)', got == ['hb-699', 'hb-700', 'hb-701'] and all(x['_source'] == 'workers-logs' and x['scriptVersion']['id'].startswith('3cf4cd12') and x['outcome'] == 'ok' and x['event']['response']['status'] == 200 for x in obs), got)
    chk('C4 phase 2 at b+120s: agreement passes -> gap RECOVERED, window INTACT, no false abort', c3 == 0 and o3['result'] == 'INTACT' and all(g['status'] == 'RECOVERED' for g in g3), [(g['from'], g['to'], g['status'], g['notes']) for g in g3])
    c4, o4 = rev2(b + 125000); chk('C5 re-evaluation after recovery stays INTACT (no re-query, no double merge)', c4 == 0 and len(open(f'{R}/obs.jsonl').read().splitlines()) == len(obs), o4.get('result'))
print(f'WORKERS LOGS SUITE: {sum(res)}/{len(res)} passed', flush=True)
