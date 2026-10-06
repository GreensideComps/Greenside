#!/usr/bin/env python3
"""H5 stage.py: creates the 750 QAL drafts for L1 (Stage 3 only, separately approved; NOT run in Stage 1).

  stage.py dry     --product PRODUCT.json --out DIR      payloads only, no network
  stage.py execute --product PRODUCT.json --out DIR --confirm STAGE-QAL-750-DRAFTS

Each draft: exactly one line (the L1 variant, the planned quantity), a 100% draft discount (order total 0.00), tags
[qa-load, QAL, QAL-NNNN-qQ], a fixed note; NO customer, email, phone, addresses or shipping line. The route is the one proven by
orders #1011/#1012 (100% draft discount -> PAID, no transaction, allocated by webhook).
Refusals: wrong phrase; fixture product check (section below); any existing QAL-tagged draft; plan template not exactly the
deterministic one. Execution: at most 2 creates per second; never retried; the first error stops staging (the partial result is
recorded and NO bound plan is produced). Output: plan-bound.json + plan-bound.sha256 (sha256 of the canonical bound plan)."""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import (CAPACITY, CONFIRM_STAGE, DISCOUNT_TITLE, ORDER_TAG, PREFIX, PRODUCT_HANDLE, PRODUCT_TAGS, PRODUCT_TITLE,  # noqa: E402
                    RETIRED_COMPETITIONS, is_known_canary_draft, START_NUMBER, UNIT_PRICE, UNITS, Refused, canonical, plan_hash, plan_template,
                    scrub, validate_plan, write_json)

DRAFT_CREATE = ("mutation CreateL1Draft($input: DraftOrderInput!) { draftOrderCreate(input: $input) { draftOrder { id name status "
                "tags totalPriceSet { shopMoney { amount } } } userErrors { field message } } }")
LIST_QAL = ('{ qal: draftOrders(first: 20, query: "tag:QAL") { nodes { id name status tags } } '
            'qaload: draftOrders(first: 20, query: "tag:qa-load") { nodes { id name status tags } } }')
NOTE = "Greenside QA L1 allocator load test. QA ONLY. Not a sale. No customer. Total 0.00 (100% discount)."
INPUT_KEYS = {"lineItems", "appliedDiscount", "tags", "note"}
MAX_RATE_PER_S = 2.0


def existing_load_drafts(status, body):
    """Drafts matching tag:QAL or tag:qa-load, minus ONLY the known canary draft (exact GID). Raises if the listing failed."""
    data = (body or {}).get("data") or {}
    lists = [((data.get(k) or {}).get("nodes")) for k in ("qal", "qaload")]
    if status != 200 or any(x is None for x in lists) or (body or {}).get("errors"):
        raise Refused("stage: could not list existing QAL/qa-load drafts")
    seen = {}
    for n in lists[0] + lists[1]:
        seen[n.get("id")] = n
    return [n for gid, n in seen.items() if not is_known_canary_draft(gid)]


def check_product(p):
    """Problems with the L1 fixture product snapshot. p: {id, title, handle, status, publishedAt, onlineStoreUrl, tags, metafields
    {key: value}, variants: [{id, price, inventoryPolicy, inventoryQuantity, tracked, requiresShipping}], publications: int}."""
    probs = []
    cid = str(p.get("id", "")).rsplit("/", 1)[-1]
    if cid in RETIRED_COMPETITIONS:
        probs.append(f"product {cid} is a retired fixture ({RETIRED_COMPETITIONS[cid]})")
    for k, want in (("title", PRODUCT_TITLE), ("handle", PRODUCT_HANDLE), ("status", "ACTIVE")):
        if p.get(k) != want:
            probs.append(f"{k} is {p.get(k)!r}, expected {want!r}")
    if p.get("publishedAt") is not None or p.get("onlineStoreUrl") is not None:
        probs.append("product is published to the Online Store")
    if p.get("publications") != 0:
        probs.append(f"publications {p.get('publications')!r}, expected 0 on every channel")
    if not set(PRODUCT_TAGS) <= set(p.get("tags") or []):
        probs.append(f"tags {p.get('tags')} lack {sorted(PRODUCT_TAGS)}")
    mf = p.get("metafields") or {}
    for k, want in (("entry_prefix", PREFIX), ("entry_start_number", str(START_NUMBER)), ("entries_total", str(CAPACITY)),
                    ("skill_mode", "none")):
        if str(mf.get(k)) != want:
            probs.append(f"custom.{k} is {mf.get(k)!r}, expected {want!r}")
    v = p.get("variants") or []
    if len(v) != 1:
        probs.append(f"{len(v)} variants, expected exactly 1")
    else:
        v = v[0]
        for k, want in (("price", UNIT_PRICE), ("inventoryPolicy", "DENY"), ("inventoryQuantity", UNITS), ("tracked", True),
                        ("requiresShipping", False)):
            if v.get(k) != want:
                probs.append(f"variant {k} is {v.get(k)!r}, expected {want!r}")
    return probs


def build_input(row, variant_gid):
    inp = {"lineItems": [{"variantId": variant_gid, "quantity": row["qty"]}],
           "appliedDiscount": {"valueType": "PERCENTAGE", "value": 100.0, "title": DISCOUNT_TITLE},
           "tags": [ORDER_TAG, PREFIX, row["tag"]], "note": NOTE}
    assert set(inp) == INPUT_KEYS            # nothing else: no customer, email, phone, address or shipping line
    return inp


def stage(template, product, client, clock, out_dir, execute=False, confirm=None, secrets=()):
    if template != plan_template():
        raise Refused("plan template is not the deterministic L1 template")
    probs = validate_plan(template) + check_product(product)
    if probs:
        raise Refused("fixture/plan: " + "; ".join(probs))
    variant = product["variants"][0]["id"]
    payloads = [{"index": r["index"], "input": build_input(r, variant)} for r in template["rows"]]
    os.makedirs(out_dir, exist_ok=True)
    write_json(os.path.join(out_dir, "stage-payloads.json"), payloads)
    if not execute:
        return {"mode": "dry", "payloads": len(payloads)}
    if confirm != CONFIRM_STAGE:
        raise Refused("stage: confirmation phrase missing or wrong")
    st, _, body = client.post(LIST_QAL)
    nodes = existing_load_drafts(st, body)
    if nodes:
        raise Refused(f"stage: unexpected QAL/qa-load draft(s) exist ({', '.join(str(n.get('name')) for n in nodes[:5])}); "
                      "refusing to stage twice")
    bound = dict(template, kind="L1-plan-bound", competition_id=str(product["id"]).rsplit("/", 1)[-1],
                 product_gid=product["id"], variant_gid=variant, rows=[])
    log = os.path.join(out_dir, "stage-evidence.jsonl")
    for r, pl in zip(template["rows"], payloads):
        t0 = clock.now()
        rec = {"index": r["index"], "qty": r["qty"], "tag": r["tag"], "t": t0}
        try:
            st, h, body = client.post(DRAFT_CREATE, {"input": pl["input"]})
        except Exception as e:
            rec.update(outcome="UNKNOWN", error=type(e).__name__)
            _log(log, rec, secrets)
            return {"mode": "execute", "status": "INCOMPLETE", "created": len(bound["rows"]), "stopped_at": r["index"]}
        d = (((body or {}).get("data") or {}).get("draftOrderCreate") or {})
        do = d.get("draftOrder") or {}
        total = (((do.get("totalPriceSet") or {}).get("shopMoney") or {}).get("amount"))
        ok = (st == 200 and not (body or {}).get("errors") and not d.get("userErrors") and do.get("status") == "OPEN"
              and str(total) in ("0.0", "0.00", "0") and r["tag"] in (do.get("tags") or []))
        rec.update(outcome="CREATED" if ok else "ERROR", http_status=st, request_id=(h or {}).get("x-request-id"),
                   draft_id=do.get("id"), draft_name=do.get("name"), total=total, user_errors=d.get("userErrors"))
        _log(log, rec, secrets)
        if not ok:
            return {"mode": "execute", "status": "INCOMPLETE", "created": len(bound["rows"]), "stopped_at": r["index"]}
        bound["rows"].append(dict(r, draft_id=do["id"], draft_name=do.get("name")))
        clock.sleep(max(0.0, 1.0 / MAX_RATE_PER_S - (clock.now() - t0)))
    probs = validate_plan(bound, bound=True)
    if probs:
        raise Refused("bound plan invalid: " + "; ".join(probs))
    write_json(os.path.join(out_dir, "plan-bound.json"), bound)
    sha = plan_hash(bound)
    with open(os.path.join(out_dir, "plan-bound.sha256"), "w") as f:
        f.write(sha + "\n")
    return {"mode": "execute", "status": "COMPLETE", "created": len(bound["rows"]), "plan_sha256": sha}


def _log(path, rec, secrets):
    with open(path, "a") as f:
        f.write(scrub(canonical(rec), secrets) + "\n")


def main(argv=None):
    p = argparse.ArgumentParser(prog="stage.py")
    p.add_argument("mode", choices=["dry", "execute"]); p.add_argument("--product", required=True)
    p.add_argument("--out", required=True); p.add_argument("--confirm")
    a = p.parse_args(argv)
    product = json.load(open(a.product))
    try:
        client = None
        if a.mode == "execute":
            from shop import Client
            client = Client("stress_driver", {DRAFT_CREATE, LIST_QAL})

        class RealClock:
            now = staticmethod(time.time)
            sleep = staticmethod(time.sleep)
        r = stage(plan_template(), product, client, RealClock(), a.out, a.mode == "execute", a.confirm,
                  getattr(client, "secrets", ()))
        print(canonical(r))
        return 0 if a.mode == "dry" or r.get("status") == "COMPLETE" else 3
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
