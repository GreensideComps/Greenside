S=__SCRATCHPAD__; N=$S/b3stress; T=$S/b3stress-selftest; rm -rf $T; mkdir -p $T; python3 - "$S" "$N" "$T" <<'EOF'
import json, subprocess, copy, sys, os, shutil, itertools
S, N, T = sys.argv[1:]; fails = 0; count = 0
def chk(name, ok, extra=''):
    global fails, count; count += 1; fails += not ok; print(f"{'PASS' if ok else 'FAIL'} {name} {extra}")
CID = '99990001'; PG = f'gid://shopify/Product/{CID}'
cfg = json.load(open(f'{N}/stress.json')); cfg.update(competition_id=CID, product_gid=PG); json.dump(cfg, open(f'{T}/stress.json', 'w'))
L = lambda d: {k: (json.loads(v) if isinstance(v, str) and k != 'marker' else v) for k, v in d.items()}
ref = L(json.load(open(f'{S}/b3live3/d1-post.json')))
ref['competitions'].append({'competition_id': CID, 'shop_domain': '7r5csb-1j.myshopify.com', 'product_gid': PG, 'prefix': 'QAH', 'start_number': 1001, 'capacity': 6, 'pad_width': 4, 'handle_snapshot': 'h', 'title_snapshot': 't', 'skill_question_snapshot': None, 'skill_answers_snapshot': None, 'skill_answer_correct_snapshot': None, 'status': 'OPEN', 'pool_built_at': 'p', 'frozen_at': None, 'created_at': 'p', 'updated_at': 'p'})
for s in range(1001, 1007): ref['entries'].append([CID, s, f'QAH{s}', 'AVAILABLE', None, None, None, None, 0, None, None, None])
json.dump(ref, open(f'{T}/dref.json', 'w'))
E0 = len(ref['events']); A0 = len(ref['allocations'])
def claim(d, oid, q, verdict='NOT_REQUIRED', comp=CID, evtype='ALLOCATED', seqs=None, source='webhook'):
    aid = f'alloc{oid}'.ljust(64, '0')
    d['allocations'].append([aid, '7r5csb-1j.myshopify.com', comp, oid, f'#{oid}', 't', f'L{oid}', 'v', None, 'unknown', q, 1, q, q, None, None, None, verdict, None, None, 100, 100 * q, '{}', 'ALLOCATED', source, 'PENDING', 0, 't', 't'])
    free = [e for e in d['entries'] if e[0] == CID and e[3] == 'AVAILABLE']
    take = [e for e in free if e[1] in seqs] if seqs else free[:q]
    for e in take:
        e[3:12] = ['ALLOCATED', aid, oid, f'L{oid}', None, 1, 'T', None, None]
        eid = (d['events'][-1][0] if d['events'] else 0) + 1
        d['events'].append([eid, 'T', CID, e[1], e[2], aid, 1, evtype, 'AVAILABLE', 'ALLOCATED', oid, None, None, 'system:' + source, 'r', 'w', '{}'])
    d['sqlite_seq'] = [[n, v + len(take) if n == 'entry_event' else v] for n, v in d['sqlite_seq']]
    d['integrity']['events_total'] = len(d['events']); d['integrity']['allocations_total'] = len(d['allocations'])
def drun(name, steps, want, complete=None):
    d = copy.deepcopy(ref)
    for s in steps: s(d)
    json.dump(d, open(f'{T}/dnow.json', 'w'))
    r = subprocess.run(['python3', f'{N}/dstate.py', f'{T}/dref.json', f'{T}/dnow.json', f'{T}/stress.json'], capture_output=True, text=True); o = json.loads(r.stdout)
    chk(f'dstate {name}', (r.returncode == 0) == want and (complete is None or o['complete'] == complete), f"alloc={o['allocated']} complete={o['complete']} dev={str(o['deviations'][:1])[:110]}")
    return o
drun('reference (nothing yet)', [], True, False)
for perm in [('1', '2', '3', '4'), ('3', '1', '4', '2'), ('4', '3', '2', '1')]:
    q = {'1': 1, '2': 1, '3': 2, '4': 2}
    o = drun(f'full pool, commit order {"".join(perm)}', [lambda d, p=p: claim(d, p, q[p]) for p in perm], True, True)
chk('  -> blocks tile 1001-1006 in commit order', [a['entries'] for a in o['allocations']] == [['QAH1001', 'QAH1002'], ['QAH1003', 'QAH1004'], ['QAH1005'], ['QAH1006']], str([a['entries'] for a in o['allocations']]))
drun('partial: 2 orders mid-burst', [lambda d: claim(d, '3', 2), lambda d: claim(d, '1', 1)], True, False)
drun('sweep claimed one order (source reconcile)', [lambda d: claim(d, '3', 2, source='reconcile'), lambda d: claim(d, '1', 1)], True, False)
drun('number skipped (claims 1002 while 1001 free)', [lambda d: claim(d, '1', 1, seqs=[1002])], False)
drun('non-contiguous block', [lambda d: claim(d, '1', 1), lambda d: claim(d, '3', 2, seqs=[1002, 1004])], False)
drun('5th allocation / quantity outside multiset', [lambda d: claim(d, '1', 1), lambda d: claim(d, '2', 1), lambda d: claim(d, '5', 1)], False)
drun('quantity 3', [lambda d: claim(d, '1', 3)], False)
drun('verdict CORRECT', [lambda d: claim(d, '1', 1, verdict='CORRECT')], False)
drun('allocation on QAE', [lambda d: claim(d, '1', 1, comp='15897614614902')], False)
drun('REFUSED_CAPACITY-type event', [lambda d: claim(d, '1', 1, evtype='REFUSED_CAPACITY')], False)
def dupev(d):
    claim(d, '1', 1); e = copy.deepcopy(d['events'][-1]); e[0] += 1; d['events'].append(e); d['sqlite_seq'] = [[n, v + 1 if n == 'entry_event' else v] for n, v in d['sqlite_seq']]; d['integrity']['events_total'] += 1
drun('duplicate ALLOCATED event', [dupev], False)
def heldbad(d):
    claim(d, '3', 2); d['allocations'][-1][13] = 1
drun('held_count mismatch', [heldbad], False)
def twoalloc(d):
    claim(d, '1', 1); claim(d, '2', 1); d['allocations'][-1][3] = '1'
drun('two allocations for the same order', [twoalloc], False)
def tile(d):
    claim(d, '1', 1); claim(d, '2', 1); d['events'][-1][0], d['events'][-2][0] = d['events'][-2][0], d['events'][-1][0]; d['events'][-2:] = d['events'][-2:][::-1]
drun('blocks out of commit order', [tile], False)
drun('integrity nonzero', [lambda d: claim(d, '1', 1), lambda d: d['integrity'].__setitem__('multi_held', 1)], False)
def qag(d):
    for e in d['entries']:
        if e[2] == 'QAG1003': e[3] = 'ALLOCATED'
drun('unrelated pool touched (QAG)', [qag], False)
# Shopify
sref = json.load(open(f'{S}/b3live3/shop-final.json'))
pb = copy.deepcopy(sref['product_b']); pb['id'] = PG; pb['totalInventory'] = 6; pb['variants']['nodes'][0]['inventoryQuantity'] = 6; sref['product_b'] = pb
sref['products'].append({'id': PG, 'title': 'QA — Allocator Stress Test', 'handle': 'h', 'status': 'ACTIVE', 'updatedAt': 'u0', 'totalInventory': 6}); json.dump(sref, open(f'{T}/sref.json', 'w'))
def order(d, n, q, tag='qa-stress', pid=PG, fin='PAID'):
    d['orders'].append({'id': f'gid://shopify/Order/{n}', 'name': f'#{n}', 'createdAt': 'c', 'updatedAt': 'u', 'cancelledAt': None, 'displayFinancialStatus': fin, 'displayFulfillmentStatus': 'UNFULFILLED', 'tags': [tag], 'lineItems': {'nodes': [{'id': f'gid://shopify/LineItem/{n}', 'sku': None, 'quantity': q, 'currentQuantity': q, 'product': {'id': pid}}]}})
def stock(d, t, v=None):
    d['product_b']['totalInventory'] = t; d['product_b']['variants']['nodes'][0]['inventoryQuantity'] = t if v is None else v
    for p in d['products']:
        if p['id'] == PG: p['totalInventory'] = t
def srun(name, steps, want, complete=None):
    d = copy.deepcopy(sref)
    for s in steps: s(d)
    json.dump(d, open(f'{T}/snow.json', 'w'))
    r = subprocess.run(['python3', f'{N}/shopstate.py', f'{T}/sref.json', f'{T}/snow.json', f'{T}/stress.json'], capture_output=True, text=True); o = json.loads(r.stdout)
    chk(f'shopstate {name}', (r.returncode == 0) == want and (complete is None or o['complete'] == complete), f"stock={o['stock']} exp={o['stock_expected']} complete={o['complete']} dev={str(o['deviations'][:1])[:100]}")
full = [lambda d: order(d, 1021, 1), lambda d: order(d, 1022, 1), lambda d: order(d, 1023, 2), lambda d: order(d, 1024, 2)]
srun('reference', [], True, False)
srun('all 4 orders, stock 0/0', full + [lambda d: stock(d, 0)], True, True)
srun('all 4 orders, stock mid-propagation 2/0', full + [lambda d: stock(d, 2, 0)], True, False)
srun('2 orders mid-burst, stock 4', full[:2] + [lambda d: stock(d, 4)], True, False)
srun('5th order', full + [lambda d: order(d, 1025, 1), lambda d: stock(d, 0)], False)
srun('untagged order', [lambda d: order(d, 1021, 1, tag='x'), lambda d: stock(d, 5)], False)
srun('order for QAG', [lambda d: order(d, 1021, 1, pid='gid://shopify/Product/15902724817270')], False)
srun('quantity 3', [lambda d: order(d, 1021, 3), lambda d: stock(d, 3)], False)
srun('stock drop without orders', [lambda d: stock(d, 5)], False)
srun('stock below expected (oversell)', full + [lambda d: stock(d, -1)], False)
srun('#1016 changed', [lambda d: [o.__setitem__('cancelledAt', 'x') for o in d['orders'] if o['name'] == '#1016']], False)
# cstate
V = 'ffff'
def hook(ts, topic, og, ver=V, status=200, lines=1):
    return {'eventTimestamp': ts, 'wallTime': 700, 'outcome': 'ok', 'exceptions': [], 'scriptVersion': {'id': ver}, 'logs': [{'message': [json.dumps({'level': 'info', 'event': 'webhook_processed', 'topic': topic, 'order_gid': og, 'lines': lines})]}],
            'event': {'request': {'url': 'https://x.workers.dev/webhooks/' + topic, 'headers': {'x-shopify-topic': topic, 'x-shopify-hmac-sha256': 'h', 'x-shopify-shop-domain': '7r5csb-1j.myshopify.com', 'x-shopify-api-version': '2026-07', 'x-shopify-webhook-id': f'w{ts}'}}, 'response': {'status': status}}}
def sweep(ts, claims=(), in_scope=4, mism=None, cron='*/15 * * * *', mode='trailing', released=None, comp=CID, wall=3000):
    conv = [{'event': 'reconcile_converged', 'order_gid': og, 'reason': 'x', 'outcomes': [{'competition_id': comp, 'claimed': [{'n': 1}] * q, 'released': released}]} for og, q in claims]
    m = len(conv) if mism is None else mism
    su = {'event': 'reconcile_summary', 'dry_run': False, 'report': False, 'run_id': f'r{ts}', 'mode': mode, 'search': 's', 'orders_seen': in_scope, 'in_scope': in_scope, 'mismatched': m, 'converged_orders': len(conv), 'claimed': sum(q for _, q in claims), 'released': 0, 'refused_not_open': 0, 'unreadable': 0, 'errors': 0, 'truncated': False, 'aged_held': 0}
    return {'eventTimestamp': ts, 'wallTime': wall, 'outcome': 'ok', 'exceptions': [], 'scriptVersion': {'id': V}, 'logs': [{'message': [json.dumps(x)]} for x in conv + [su]], 'event': {'cron': cron, 'scheduledTime': ts}}
O = [f'gid://shopify/Order/{n}' for n in (1021, 1022, 1023, 1024)]
def burst(t=5000):
    ev = []
    for i, o in enumerate(O): ev += [hook(t + 150 * i, 'orders/create', o), hook(t + 900 + 150 * i, 'orders/paid', o)]
    return ev
def case(name, evs, want_dev, armed=1000, bound=O, done=None, check=None):
    d = f'{T}/c'; shutil.rmtree(d, ignore_errors=True); os.makedirs(d); shutil.copy(f'{N}/sweeps.py', d); shutil.copy(f'{T}/stress.json', d)
    open(f'{d}/pre.jsonl', 'w').write('\n'.join(json.dumps(e) for e in evs))
    if armed: open(f'{d}/qag-start', 'w').write(str(armed))
    if bound: open(f'{d}/bound-orders.txt', 'w').write('\n'.join(bound) + '\n')
    if done: open(f'{d}/complete-ms', 'w').write(str(done))
    o = json.loads(subprocess.run(['python3', f'{N}/cstate.py', d, '0', V], capture_output=True, text=True).stdout)
    chk(f'cstate {name}', bool(o['deviations']) == want_dev and (check is None or check(o)), f"hooks_done={o['hooks_done']} zero={o['zero_sweeps_after_complete']} dev={str(o['deviations'][:1])[:100]}")
    return d
case('8-webhook burst for the 4 bound orders', burst(), False, check=lambda o: o['hooks_done'])
case('duplicate deliveries tolerated', burst() + [hook(9000, 'orders/paid', O[2])], False)
case('webhook before arming', burst(500), True, armed=5000)
case('5th order', burst() + [hook(9000, 'orders/create', 'gid://shopify/Order/1025')], True, bound=None)
case('order outside bound set', burst(), True, bound=O[:3])
case('unexpected topic', [hook(5000, 'refunds/create', O[0])], True)
case('wrong version', [hook(5000, 'orders/create', O[0], ver='x')], True)
case('sweep races webhooks and claims for 2 bound orders', burst() + [sweep(5200, claims=[(O[0], 1), (O[2], 2)])], False)
case('sweep claims for a non-stress order', burst() + [sweep(5200, claims=[('gid://shopify/Order/9', 1)])], True)
case('sweep claims on another competition', burst() + [sweep(5200, claims=[(O[0], 1)], comp='15897614614902')], True)
case('sweep claims more than capacity', burst() + [sweep(5200, claims=[(O[0], 1), (O[1], 1), (O[2], 2), (O[3], 2)]), sweep(6200, claims=[(O[0], 1)])], True)
case('qualifying zero sweep after complete', burst() + [sweep(20000)], False, done=10000, check=lambda o: o['zero_sweeps_after_complete'] == 1)
case('in_scope below order count does not qualify', burst() + [sweep(20000, in_scope=2)], False, done=10000, check=lambda o: o['zero_sweeps_after_complete'] == 0)
case('claim after complete', burst() + [sweep(20000, claims=[(O[0], 1)])], True, done=10000)
# overlap
d = case('(overlap input) burst + racing sweep', burst() + [sweep(5200, claims=[(O[0], 1)])], False)
o = json.loads(subprocess.run(['python3', f'{N}/overlap.py', d, '0', '100000'], capture_output=True, text=True).stdout)
chk('overlap: burst is CONCURRENT with cross/same/sweep pairs', o['verdict'] == 'CONCURRENT' and o['counts']['cross_order'] > 0 and o['counts']['hook_sweep'] > 0, f"{o['counts']} peak={o['peak_in_flight']}")
seq = [hook(5000 + 2000 * i, 'orders/create', og) for i, og in enumerate(O)]
d = case('(overlap input) strictly sequential', seq, False)
o = json.loads(subprocess.run(['python3', f'{N}/overlap.py', d, '0', '100000'], capture_output=True, text=True).stdout)
chk('overlap: sequential arrivals are reported SEQUENTIAL', o['verdict'] == 'SEQUENTIAL' and o['counts']['cross_order'] == 0, f"{o['counts']}")
print(f'SELF-TEST: {count - fails}/{count} passed')
EOF