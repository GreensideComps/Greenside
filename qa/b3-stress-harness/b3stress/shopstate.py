#!/usr/bin/env python3
"""B3 STRESS-window Shopify checker. shopsnap.sh snapshot with product_b = the stress product.
Usage: shopstate.py REF.json NOW.json stress.json  -> prints JSON {new_orders, stock, stock_expected, complete, deviations}; exit 0/2.
Allowed difference from REF (taken after the draft orders exist, before any is completed), and nothing else:
  - at most len(order_quantities) new orders, each tagged order_tag, exactly one line: the stress product x q (currentQuantity q),
    PAID, not cancelled; the quantities are a sub-multiset of order_quantities;
  - stress product stock only: total and variant each within [start - sum(new q), start] (Shopify may update the two at slightly
    different moments); stock_expected = start - sum(new q). Stress product updatedAt.
  complete = all orders present with exactly the multiset AND stock total = variant = start - sum(q) (i.e. 0).
Every reference order (incl. #1015-#1020), QAE (product_a) and every other product: identical. Fewer than 100 orders."""
import json, sys
from collections import Counter
ref, now, cfg = json.load(open(sys.argv[1])), json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
PG = cfg['product_gid']; QTY = Counter(cfg['order_quantities']); TAG = cfg['order_tag']; START = cfg['shop_stock_start']
dev = []
if now.get('errors'): dev.append(f"shopify errors {now['errors']}")
if len(now.get('orders') or []) >= 100: dev.append('100 or more orders: new orders may not be visible')
if now.get('product_a') != ref.get('product_a'): dev.append('QAE product changed')
ro = {o['id']: o for o in ref['orders']}; no = {o['id']: o for o in now['orders']}
for k in ro:
    if no.get(k) != ro[k]: dev.append(f"existing order {ro[k]['name']} changed/missing")
new = [no[k] for k in no if k not in ro]
if len(new) > sum(QTY.values()): dev.append(f'{len(new)} new orders (max {sum(QTY.values())})')
out = []
for o in new:
    li = o['lineItems']['nodes']
    q = li[0]['quantity'] if len(li) == 1 else None
    out.append({'id': o['id'], 'name': o['name'], 'createdAt': o['createdAt'], 'qty': q, 'financial': o['displayFinancialStatus'], 'cancelledAt': o['cancelledAt'], 'tags': o['tags']})
    if len(li) != 1 or (li[0].get('product') or {}).get('id') != PG or li[0]['currentQuantity'] != li[0]['quantity']:
        dev.append(f"new order {o['name']} lines {[(l.get('product') or {}).get('id') for l in li]} (expected exactly one stress line)")
    if TAG not in (o.get('tags') or []): dev.append(f"new order {o['name']} is not tagged {TAG}")
    if o['displayFinancialStatus'] != 'PAID' or o['cancelledAt'] is not None: dev.append(f"new order {o['name']} {o['displayFinancialStatus']} cancelled={o['cancelledAt']}")
qc = Counter(x['qty'] for x in out)
if qc - QTY: dev.append(f'new order quantities {dict(qc)} not within {dict(QTY)}')
sold = sum(x['qty'] or 0 for x in out); expected = START - sold
def strip_b(p):
    p = json.loads(json.dumps(p)); p.pop('updatedAt', None); p.pop('totalInventory', None)
    for v in p['variants']['nodes']: v.pop('inventoryQuantity', None)
    return p
b0, b1 = ref.get('product_b'), now.get('product_b'); tot = var = None
if not b0 or b0.get('id') != PG: dev.append('reference product_b is not the stress product')
if not b1 or strip_b(b1) != strip_b(b0): dev.append('stress product changed (other than stock/updatedAt)')
else:
    tot, var = b1['totalInventory'], b1['variants']['nodes'][0]['inventoryQuantity']
    if not (expected <= tot <= START) or not (expected <= var <= START): dev.append(f'stress stock {tot}/{var} outside [{expected}, {START}]')
if b0 and (b0['totalInventory'], b0['variants']['nodes'][0]['inventoryQuantity']) != (START, START): dev.append('reference stress stock is not the start value')
rp = {p['id']: p for p in ref['products']}; np_ = {p['id']: p for p in now['products']}
if set(rp) != set(np_): dev.append('product list changed')
for k in rp:
    a, b = dict(rp[k]), dict(np_.get(k) or {})
    if k == PG:
        a.pop('updatedAt', None); b.pop('updatedAt', None)
        if not (expected <= (b.get('totalInventory') if b.get('totalInventory') is not None else -1) <= START): dev.append(f"stress totalInventory {b.get('totalInventory')}")
        a.pop('totalInventory', None); b.pop('totalInventory', None)
    if a != b: dev.append(f"product {rp[k]['title']} changed")
complete = qc == QTY and tot == expected and var == expected
print(json.dumps({'new_orders': out, 'stock': [tot, var], 'stock_expected': expected, 'complete': complete, 'deviations': dev}))
sys.exit(2 if dev else 0)
