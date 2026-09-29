#!/usr/bin/env python3
"""B3 STRESS-window D1 checker. Checked on EVERY snapshot, including partial progress mid-burst.
Usage: dstate.py REF.json NOW.json stress.json  -> prints JSON {allocated, allocations, orders, complete, deviations}; exit 0 clean, 2 deviation.
Allowed difference from REF (taken after the QAH registration), and nothing else:
  - new allocations ONLY on the stress competition, at most len(order_quantities) of them, from at most that many distinct orders;
    each: entries_per_unit 1, ordered_quantity = target = held, quantity in the allowed multiset (as a sub-multiset overall),
    skill_verdict NOT_REQUIRED with no rule/judged_at/question/answer, status ALLOCATED, source webhook|reconcile;
  - stress pool rows AVAILABLE -> ALLOCATED only: allocation_seq 1, allocated_at set, never released, held by exactly one of the new
    allocations with its order/line; per allocation held == its pool rows; blocks contiguous;
  - the allocated set is always a PREFIX of the pool (lowest numbers first: nothing skipped), never above capacity;
    blocks tile the prefix in commit order (order of each allocation's first ALLOCATED event);
  - new events: ONLY 'ALLOCATED', exactly one per allocated pool row (seq, allocation_seq 1), matching allocation and order; no other
    event type (no REFUSED_CAPACITY, RELEASED, ...); sqlite_sequence.entry_event advances by exactly those events.
Competitions, every other pool (incl. PUT summary), every existing allocation and event, webhook_delivery: identical. Integrity: all 0.
complete = every pool row allocated AND the allocation quantities equal the multiset exactly."""
import json, sys
from collections import Counter
ref, now, cfg = json.load(open(sys.argv[1])), json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
C = cfg['competition_id']; CAP = cfg['capacity']; START = cfg['start_number']; QTY = Counter(cfg['order_quantities']); NORD = len(cfg['order_quantities'])
P = lambda d, k: d[k] if not isinstance(d[k], str) else json.loads(d[k])
dev = []
if P(now, 'competitions') != P(ref, 'competitions'): dev.append('competitions changed')
if P(now, 'put_summary') != P(ref, 'put_summary'): dev.append('PUT pool changed')
if P(now, 'webhook_deliveries') != P(ref, 'webhook_deliveries'): dev.append('webhook_delivery changed')
integ = P(now, 'integrity')
for k, v in integ.items():
    if k not in ('events_total', 'allocations_total') and v != 0: dev.append(f'integrity {k}={v}')
ra = {r[0]: r for r in P(ref, 'allocations')}; na = {r[0]: r for r in P(now, 'allocations')}
for k in ra:
    if na.get(k) != ra[k]: dev.append(f'existing allocation {k[:8]} changed/missing')
new = {k: v for k, v in na.items() if k not in ra}
re_ = {(r[0], r[1]): r for r in P(ref, 'entries')}; ne = {(r[0], r[1]): r for r in P(now, 'entries')}
if set(re_) != set(ne): dev.append('entry set changed')
pool_ref = sorted([r for k, r in re_.items() if k[0] == C], key=lambda r: r[1])
if len(pool_ref) != CAP or any(r[3] != 'AVAILABLE' or r[8] != 0 for r in pool_ref) or [r[1] for r in pool_ref] != list(range(START, START + CAP)):
    dev.append('reference stress pool is not exactly CAP fresh AVAILABLE rows')
changed = [ne[k] for k in ne if k in re_ and ne[k] != re_[k]]
for e in changed:
    if e[0] != C: dev.append(f'non-stress entry changed: {e[2]}')
rv, nv = P(ref, 'events'), P(now, 'events')
if nv[:len(rv)] != rv: dev.append('existing events changed')
nev = nv[len(rv):]
rs = dict(map(tuple, P(ref, 'sqlite_seq'))); ns = dict(map(tuple, P(now, 'sqlite_seq')))
if {k: v for k, v in ns.items() if k != 'entry_event'} != {k: v for k, v in rs.items() if k != 'entry_event'}: dev.append('sqlite_sequence changed')
if ns.get('entry_event', 0) != rs.get('entry_event', 0) + len(nev): dev.append('entry_event sequence does not match new events')
# alloc row: 2 comp,3 order,6 line,9 route,10 qty,11 per_unit,12 target,13 held,14 q,15 a,16 correct,17 verdict,18 judged,19 rule,23 status,24 source
# entry row: 0 comp,1 seq,2 entry,3 status,4 alloc,5 order,6 line,7 cust,8 alloc_seq,9 allocated_at,10 released_at,11 reason
# event row: 0 id,1 at,2 comp,3 seq,4 entry,5 alloc,6 alloc_seq,7 type,8 from,9 to,10 order,11 cust,12 reason,13 actor,14 run,15 webhook,16 detail
if len(new) > NORD: dev.append(f'{len(new)} new allocations (max {NORD})')
orders = sorted({a[3] for a in new.values()})
if len(orders) != len(new): dev.append('more than one allocation for the same order')
qc = Counter(a[10] for a in new.values())
if qc - QTY: dev.append(f'allocation quantities {dict(qc)} are not within the expected multiset {dict(QTY)}')
for k, a in new.items():
    if a[2] != C: dev.append(f'new allocation {k[:8]} on competition {a[2]}')
    if (a[11], a[12], a[13]) != (1, a[10], a[10]): dev.append(f'allocation {a[4]} per_unit/target/held {a[11]}/{a[12]}/{a[13]} for qty {a[10]}')
    if (a[17], a[18], a[19], a[16], a[14], a[15]) != ('NOT_REQUIRED', None, None, None, None, None): dev.append(f'allocation {a[4]} skill fields {a[14:20]}')
    if a[23] != 'ALLOCATED' or a[24] not in ('webhook', 'reconcile'): dev.append(f'allocation {a[4]} status/source {a[23]}/{a[24]}')
pool_now = sorted([r for k, r in ne.items() if k[0] == C], key=lambda r: r[1])
alloc_rows = [r for r in pool_now if r[3] == 'ALLOCATED']
if any(r[3] not in ('AVAILABLE', 'ALLOCATED') for r in pool_now): dev.append('stress pool row in a state other than AVAILABLE/ALLOCATED')
for r in pool_now:
    if r[3] == 'AVAILABLE' and (r != re_[(r[0], r[1])]): dev.append(f'{r[2]} AVAILABLE but changed')
    if r[3] == 'ALLOCATED':
        a = new.get(r[4])
        if not a: dev.append(f'{r[2]} held by unknown allocation {str(r[4])[:8]}')
        elif (r[5], r[6], r[8]) != (a[3], a[6], 1) or not r[9] or r[10] or r[11]: dev.append(f'{r[2]} state {r}')
seqs = [r[1] for r in alloc_rows]
if seqs != list(range(START, START + len(seqs))): dev.append(f'allocated numbers are not the lowest-first prefix: {[r[2] for r in alloc_rows]}')
if len(alloc_rows) > CAP: dev.append('over capacity')
held = Counter(r[4] for r in alloc_rows)
for k, a in new.items():
    if held.get(k, 0) != a[13]: dev.append(f'allocation {a[4]} held {a[13]} but pool rows {held.get(k, 0)}')
    s = sorted(r[1] for r in alloc_rows if r[4] == k)
    if s and s != list(range(s[0], s[0] + len(s))): dev.append(f'allocation {a[4]} block not contiguous {s}')
if sum(a[13] for a in new.values()) != len(alloc_rows): dev.append('sum of held != allocated pool rows')
if any(v[7] != 'ALLOCATED' for v in nev): dev.append(f'non-ALLOCATED new events: {sorted({v[7] for v in nev})}')
keys = Counter((v[3], v[6]) for v in nev)
if any(c > 1 for c in keys.values()): dev.append(f'duplicate ALLOCATED events {[k for k, c in keys.items() if c > 1]}')
by_seq = {(v[3]): v for v in nev}
if len(nev) != len(alloc_rows): dev.append(f'{len(nev)} new events for {len(alloc_rows)} allocated numbers')
for r in alloc_rows:
    v = by_seq.get(r[1])
    if not v or (v[2], v[4], v[5], v[6], v[8], v[9], v[10]) != (C, r[2], r[4], 1, 'AVAILABLE', 'ALLOCATED', r[5]) or v[13] not in ('system:webhook', 'system:reconcile') or v[1] != r[9]:
        dev.append(f'event for {r[2]}: {v}')
first_ev = {}
for v in nev: first_ev.setdefault(v[5], v[0])
tiling = [held_alloc for held_alloc, _ in sorted(first_ev.items(), key=lambda kv: kv[1])]
expect = []
for aid in tiling: expect += [aid] * held.get(aid, 0)
if [r[4] for r in alloc_rows] != expect: dev.append('blocks do not tile the pool in commit order')
if integ.get('allocations_total') != len(na) or integ.get('events_total') != len(nv): dev.append('integrity totals mismatch')
complete = len(alloc_rows) == CAP and qc == QTY
allocs = [{'allocation_id': k, 'order_id': a[3], 'order_name': a[4], 'qty': a[10], 'held': a[13], 'source': a[24], 'entries': [r[2] for r in alloc_rows if r[4] == k],
           'first_event': first_ev.get(k), 'entry_route': a[9]} for k, a in sorted(new.items(), key=lambda kv: first_ev.get(kv[0], 1 << 60))]
print(json.dumps({'allocated': len(alloc_rows), 'allocations': allocs, 'orders': orders, 'complete': complete, 'new_events': len(nev),
                  'totals': {'allocations': len(na), 'events': len(nv)}, 'deviations': dev}))
sys.exit(2 if dev else 0)
