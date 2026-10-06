#!/usr/bin/env python3
"""H8 shopsnapl.py: the L1 post-drain Shopify export (allocator app, READ-ONLY).

During the load window NO allocator-app order read is allowed (it would spend the bucket under test); the only allocator read
then is sampler.py. This tool therefore refuses while the window is open: DIR/load-<cid>.done exists (the driver started) and
the guard has not verified the restore (guardl.json restore_verified true). After that it pages every tag:qa-load order, 100 per
page, and checks the population against the bound plan.

  shopsnapl.py export --state-dir DIR --plan BOUND.json --out ORDERS.json"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import ORDER_TAG, Refused, canonical, parse_tag, read_json, write_json  # noqa: E402

ORDERS_DOC = ("query L1Orders($after: String) { orders(first: 100, after: $after, query: \"tag:qa-load\", sortKey: CREATED_AT) { "
              "pageInfo { hasNextPage endCursor } nodes { id name createdAt processedAt test cancelledAt displayFinancialStatus "
              "tags totalPriceSet { shopMoney { amount } } totalTaxSet { shopMoney { amount } } "
              "lineItems(first: 5) { nodes { id quantity currentQuantity product { id } variant { id } } } } } }")
MAX_PAGES = 50


def window_open(state_dir, cid):
    started = os.path.exists(os.path.join(state_dir, f"load-{cid}.done"))
    try:
        g = read_json(os.path.join(state_dir, "guardl.json"))
    except (OSError, ValueError):
        g = {}
    return started and not g.get("restore_verified")


def export(client, state_dir, cid):
    if window_open(state_dir, cid):
        raise Refused("load window open: no allocator-app order reads until the restore is verified")
    orders, after, pages = [], None, 0
    while True:
        st, _, body = client.post(ORDERS_DOC, {"after": after})
        pages += 1
        o = ((body or {}).get("data") or {}).get("orders")
        if st != 200 or o is None or (body or {}).get("errors"):
            raise Refused(f"orders page {pages}: HTTP {st} {str((body or {}).get('errors'))[:120]}")
        orders += o.get("nodes") or []
        pi = o.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        after = pi.get("endCursor")
        if pages >= MAX_PAGES:
            raise Refused("paging did not end within 50 pages")
    ids = [x["id"] for x in orders]
    if len(set(ids)) != len(ids):
        raise Refused("duplicate orders across pages")
    return {"pages": pages, "orders": orders}


def check(orders, plan):
    """Allow-list: every tag:qa-load order maps to exactly one plan row by its QAL-NNNN-qQ tag, with one line of the L1 product
    at the planned quantity. Returns (bindings {index: order}, problems)."""
    rows = {r["index"]: r for r in plan["rows"]}
    product = plan.get("product_gid")
    bind, probs = {}, []
    for o in orders:
        tg = parse_tag(o.get("tags"))
        if ORDER_TAG not in (o.get("tags") or []):
            probs.append(f"{o.get('name')}: missing tag {ORDER_TAG}")
        if tg is None:
            probs.append(f"{o.get('name')}: no single QAL-NNNN-qQ tag (not a plan order)")
            continue
        idx, q = tg
        r = rows.get(idx)
        if r is None or r["qty"] != q:
            probs.append(f"{o.get('name')}: tag {idx}/q{q} not in the plan")
            continue
        if idx in bind:
            probs.append(f"{o.get('name')}: plan row {idx} bound twice ({bind[idx]['name']})")
            continue
        li = ((o.get("lineItems") or {}).get("nodes")) or []
        if len(li) != 1 or ((li[0].get("product") or {}).get("id")) != product or li[0].get("quantity") != r["qty"]:
            probs.append(f"{o.get('name')}: not exactly one line of the L1 product at quantity {r['qty']}")
        bind[idx] = o
    return bind, probs


def main(argv=None):
    p = argparse.ArgumentParser(prog="shopsnapl.py")
    p.add_argument("cmd", choices=["export"]); p.add_argument("--state-dir", required=True)
    p.add_argument("--plan", required=True); p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    plan = read_json(a.plan)
    try:
        if window_open(a.state_dir, plan["competition_id"]):
            raise Refused("load window open")
        from shop import Client
        r = export(Client("allocator", {ORDERS_DOC}), a.state_dir, plan["competition_id"])
        bind, probs = check(r["orders"], plan)
        write_json(a.out, {**r, "bound": len(bind), "problems": probs})
        print(canonical({"pages": r["pages"], "orders": len(r["orders"]), "bound": len(bind), "problems": len(probs)}))
        return 0 if not probs else 1
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
