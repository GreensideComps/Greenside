#!/usr/bin/env python3
"""H5 canary.py: the single side-effect canary order (Stage 2 only, separately approved; NOT run in Stage 1).

  canary.py execute --product CANARY.json --d1-competitions D1.json --l1-competition ID --confirm CANARY-QAL-L1-C-1-ORDER

One draft on the UNREGISTERED canary product, identical in shape to an L1 draft (one line, quantity 1, 100% discount, no customer /
email / phone / address / shipping, tags [qa-load, QAL-CANARY]), then one draftOrderComplete. It proves, with the QA Worker at
DRY_RUN "true": the order is PAID with a 0.00 total and no transaction; orders/create AND orders/paid are delivered (Workers Logs
dry_run_webhook lines, checked afterwards); and lets the owner check staff email, Klaviyo, UpPromote and Meta for side effects.
Refusals: wrong phrase; the canary product is registered in QA D1 or is the L1 competition or a retired fixture; product checks;
QA Worker DRY_RUN not exactly "true" (checked by the caller-supplied function, read-only Cloudflare GET); never retried."""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import (CANARY_TAG, CANARY_TITLE, CONFIRM_CANARY, DISCOUNT_TITLE, ORDER_TAG, RETIRED_COMPETITIONS, UNIT_PRICE,  # noqa: E402
                    Refused, canonical, scrub, write_json)
from load import MUTATION as COMPLETE, classify  # noqa: E402
from stage import DRAFT_CREATE, NOTE  # noqa: E402


def check_canary(product, d1_competitions, l1_competition):
    probs = []
    cid = str(product.get("id", "")).rsplit("/", 1)[-1]
    if cid in {str(c) for c in d1_competitions}:
        probs.append(f"canary product {cid} is REGISTERED in QA D1; the allocator would allocate it")
    if cid == str(l1_competition):
        probs.append("canary product is the L1 competition")
    if cid in RETIRED_COMPETITIONS:
        probs.append("canary product is a retired fixture")
    if product.get("title") != CANARY_TITLE:
        probs.append(f"title {product.get('title')!r}, expected {CANARY_TITLE!r}")
    if product.get("publishedAt") is not None or product.get("publications") != 0:
        probs.append("canary product is published")
    v = (product.get("variants") or [{}])
    if len(v) != 1 or v[0].get("price") != UNIT_PRICE or v[0].get("requiresShipping") is not False:
        probs.append("canary variant must be exactly one, price 1.00, requiresShipping false")
    return probs


def canary_input(variant_gid):
    return {"lineItems": [{"variantId": variant_gid, "quantity": 1}],
            "appliedDiscount": {"valueType": "PERCENTAGE", "value": 100.0, "title": DISCOUNT_TITLE},
            "tags": [ORDER_TAG, CANARY_TAG], "note": NOTE}


def run_canary(product, d1_competitions, l1_competition, client, dry_run_value, out_dir, confirm, clock):
    if confirm != CONFIRM_CANARY:
        raise Refused("canary: confirmation phrase missing or wrong")
    probs = check_canary(product, d1_competitions, l1_competition)
    if probs:
        raise Refused("canary: " + "; ".join(probs))
    if dry_run_value() != "true":
        raise Refused("canary: QA Worker DRY_RUN is not exactly \"true\"")
    os.makedirs(out_dir, exist_ok=True)
    ev = {"t": clock.now(), "product": product.get("id")}
    st, h, body = client.post(DRAFT_CREATE, {"input": canary_input(product["variants"][0]["id"])})
    d = (((body or {}).get("data") or {}).get("draftOrderCreate") or {})
    do = d.get("draftOrder") or {}
    total = (((do.get("totalPriceSet") or {}).get("shopMoney") or {}).get("amount"))
    ev["create"] = {"http_status": st, "request_id": (h or {}).get("x-request-id"), "draft_id": do.get("id"), "total": total,
                    "user_errors": d.get("userErrors")}
    if st != 200 or d.get("userErrors") or do.get("status") != "OPEN" or str(total) not in ("0.0", "0.00", "0"):
        ev["result"] = "CREATE_FAILED"
        write_json(os.path.join(out_dir, "canary.json"), json.loads(scrub(canonical(ev), getattr(client, "secrets", []))))
        return ev
    try:
        st, h, body = client.post(COMPLETE, {"id": do["id"]})
        outcome, rid = classify(st, h, body)
    except Exception as e:
        outcome, rid = "UNKNOWN", None
        ev["error"] = type(e).__name__
    ev["complete"] = {"outcome": outcome, "request_id": rid, "t": clock.now()}
    ev["result"] = "COMPLETED" if outcome == "SUCCESS" else outcome
    ev["follow_up"] = ["Workers Logs: dry_run_webhook for orders/create AND orders/paid for this order",
                       "Shopify (read-only): displayFinancialStatus PAID, total 0.00, no transactions",
                       "Owner: staff new-order email received or not; Klaviyo Placed Order count unchanged; UpPromote; Meta Events"]
    write_json(os.path.join(out_dir, "canary.json"), json.loads(scrub(canonical(ev), getattr(client, "secrets", []))))
    return ev


def main(argv=None):
    p = argparse.ArgumentParser(prog="canary.py")
    p.add_argument("mode", choices=["execute"]); p.add_argument("--product", required=True)
    p.add_argument("--d1-competitions", required=True); p.add_argument("--l1-competition", required=True)
    p.add_argument("--out", required=True); p.add_argument("--confirm")
    a = p.parse_args(argv)
    try:
        from shop import Client, load_fire
        fire = load_fire()

        def dry_run_value():
            acct, tok = os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_API_TOKEN")
            st, s = fire.cf_json(fire.CloudflareTransport(), f"/client/v4/accounts/{acct}/workers/scripts/{fire.QA_WORKER}/settings", tok)
            vals = [b.get("text") for b in (s.get("result") or {}).get("bindings", []) if b.get("name") == "DRY_RUN"]
            return vals[0] if vals == ["true"] else repr(vals)
        client = Client("stress_driver", {DRAFT_CREATE, COMPLETE})

        class RealClock:
            now = staticmethod(time.time)
        r = run_canary(json.load(open(a.product)), json.load(open(a.d1_competitions)), a.l1_competition, client, dry_run_value,
                       a.out, a.confirm, RealClock())
        print(canonical({"result": r.get("result")}))
        return 0 if r.get("result") == "COMPLETED" else 3
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
