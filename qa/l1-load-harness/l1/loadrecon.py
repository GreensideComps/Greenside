#!/usr/bin/env python3
"""H9 loadrecon.py: independent L1 reconciliation. It does NOT trust any harness verdict: it re-derives every fact from raw
exports (bound plan, driver evidence, Shopify order export, draft statuses, product stock, QA D1 snapshots before/after in the
b3stress/snap.sql SW4C_SNAP form, optional Workers Logs invocations) and, separately, through recon.sql against D1 itself.

The expected population is the set of plan rows the driver COMPLETED (outcome SUCCESS with a Shopify request id). Normally all
of the plan (750 in run 1, 742 in run 2); if a stop left drafts unsent, those rows must have NO order and NO allocation.
Run 2 (amendment A1): the plan's baseline (the 8 Stage 4 orders holding QAL1001..QAL1013) is excluded from the population by exact
order GID, must be present and unchanged, and the run's numbers must follow it contiguously (QAL1014 upward).
  loadrecon.py check --in INPUT.json --out RECON.json        (INPUT: {plan, driver, orders, drafts, product, d1_pre, d1_post, wl?})
  loadrecon.py sql   --competition ID --units N --orders N [--plan PLAN]
                                                          prints each recon.sql check rendered with literals (for gs d1 query);
                                                          N = the run's own units/orders, the plan's baseline is added
Exit 0 = every check PASS."""
import argparse, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import (CAPACITY, KNOWN_CANARY, START_NUMBER, baseline_draft_gids, baseline_order_gids, entry_number,  # noqa: E402
                    is_known_canary_draft, parse_tag, plan_baseline, read_json, split_baseline_orders, split_known_canary_orders,
                    write_json)

# SW4C_SNAP column positions (b3stress/snap.sql)
E_CID, E_SEQ, E_NUM, E_ST, E_AID, E_OID, E_LID, _, E_ASEQ = range(9)
A_ID, A_CID, A_OID, A_NAME, A_LID, A_QTY, A_PER, A_TGT, A_HELD, A_UNIT, A_TOTAL, A_ST = 0, 2, 3, 4, 6, 10, 11, 12, 13, 20, 21, 23
V_ID, V_CID, V_SEQ, V_NUM, V_AID, V_ASEQ, V_TYPE, V_OID, V_RUN, V_WH = 0, 2, 3, 4, 5, 6, 7, 10, 14, 15
INTEGRITY_ZERO = ("dup_events", "audit_gaps", "ledger_drift", "orphans", "entry_order_mismatch", "dup_issue", "multi_held",
                  "dup_release", "dup_return", "freeze_audit", "bad_events")


def num(gid):
    return str(gid).rsplit("/", 1)[-1]


def money_zero(x):
    try:
        return float(x) == 0.0
    except (TypeError, ValueError):
        return False


def reconcile(inp):
    plan, cid = inp["plan"], str(inp["plan"]["competition_id"])
    rows = {r["index"]: r for r in plan["rows"]}
    drv = [r for r in inp.get("driver") or [] if r.get("kind", "request") == "request"]
    done = {r["index"] for r in drv if r.get("outcome") == "SUCCESS" and r.get("shopify_request_id")}
    unsent = set(rows) - {r["index"] for r in drv}
    units = sum(rows[i]["qty"] for i in done)
    base = plan_baseline(plan)
    bu = base["units"]
    last = START_NUMBER + bu + units - 1
    checks = []

    def ck(name, ok, detail=""):
        checks.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})

    # ---- Shopify
    orders, excluded = split_known_canary_orders(inp.get("orders") or [])   # the one known canary, by exact GID only
    ck("shopify.known_canary_excluded_by_id_only", len(excluded) <= 1, f"excluded {[o.get('id') for o in excluded]}")
    orders, base_orders = split_baseline_orders(orders, plan)               # the plan's baseline orders, by exact GID only
    ck("shopify.baseline_orders_present_by_id", sorted(o.get("id") for o in base_orders) == sorted(baseline_order_gids(plan)),
       f"baseline {len(base_orders)} of {len(base['rows'])}")
    by_idx, bad_tags = {}, []
    for o in orders:
        t = parse_tag(o.get("tags"))
        if t is None or t[0] not in rows or rows[t[0]]["qty"] != t[1] or t[0] in by_idx:
            bad_tags.append(o.get("name"))
            continue
        by_idx[t[0]] = o
    ck("shopify.orders_bind_to_plan", not bad_tags and set(by_idx) == done,
       f"orders {len(orders)}, bound {len(by_idx)}, expected {len(done)}, bad {bad_tags[:5]}, "
       f"missing {sorted(done - set(by_idx))[:5]}, unexpected {sorted(set(by_idx) - done)[:5]}")
    ck("shopify.unsent_rows_have_no_order", not (unsent & set(by_idx)), f"unsent {len(unsent)}")
    nonpaid = [o["name"] for o in by_idx.values() if o.get("displayFinancialStatus") != "PAID" or o.get("test") or o.get("cancelledAt")]
    ck("shopify.paid_not_test_not_cancelled", not nonpaid, f"{nonpaid[:5]}")
    nonzero = [o["name"] for o in by_idx.values()
               if not money_zero(((o.get("totalPriceSet") or {}).get("shopMoney") or {}).get("amount"))
               or not money_zero(((o.get("totalTaxSet") or {}).get("shopMoney") or {}).get("amount", "0"))]
    ck("shopify.zero_value", not nonzero, f"{nonzero[:5]}")
    wrongline = []
    for i, o in by_idx.items():
        li = ((o.get("lineItems") or {}).get("nodes")) or []
        if len(li) != 1 or (li[0].get("product") or {}).get("id") != plan.get("product_gid") or li[0].get("quantity") != rows[i]["qty"]:
            wrongline.append(o["name"])
    ck("shopify.one_line_planned_quantity", not wrongline, f"{wrongline[:5]}")
    drafts = {d["id"]: d.get("status") for d in inp.get("drafts") or []}
    plan_drafts = {r.get("draft_id") for r in plan["rows"]}
    extra = [g for g in drafts if g not in plan_drafts and g not in baseline_draft_gids(plan) and not is_known_canary_draft(g)]
    ck("shopify.no_unexpected_drafts", not extra, f"{len(extra)} draft(s) outside the plan: {extra[:5]}")
    ck("shopify.baseline_drafts_completed", all(drafts.get(g) == "COMPLETED" for g in baseline_draft_gids(plan)), "")
    dbad = [rows[i]["draft_name"] if "draft_name" in rows[i] else i for i in rows
            if drafts.get(rows[i].get("draft_id")) != ("COMPLETED" if i in done else "OPEN")]
    ck("shopify.draft_statuses", not dbad, f"{len(dbad)} mismatched {dbad[:5]}")
    if inp.get("product") is not None:
        stock = inp["product"].get("inventoryQuantity")
        ck("shopify.stock_matches_units", stock == inp["product"].get("stock_start") - units,
           f"stock {stock}, start {inp['product'].get('stock_start')}, units {units}")

    # ---- D1 pool
    post, pre = inp["d1_post"], inp["d1_pre"]
    ents = [e for e in post.get("entries") or [] if str(e[E_CID]) == cid]
    alloc_rows = [e for e in ents if e[E_ST] == "ALLOCATED"]
    avail_rows = [e for e in ents if e[E_ST] == "AVAILABLE"]
    ck("d1.pool_counts", len(ents) == CAPACITY and len(alloc_rows) == bu + units and len(avail_rows) == CAPACITY - bu - units
       and len(ents) == len(alloc_rows) + len(avail_rows),
       f"rows {len(ents)}, allocated {len(alloc_rows)} (expected {bu} baseline + {units}), available {len(avail_rows)}")
    seqs = sorted(e[E_SEQ] for e in alloc_rows)
    ck("d1.allocated_exact_range", seqs == list(range(START_NUMBER, last + 1)),
       f"allocated {seqs[:1]}..{seqs[-1:]} count {len(seqs)}; expected {START_NUMBER}..{last}")
    ck("d1.no_out_of_range", all(START_NUMBER <= e[E_SEQ] <= last for e in alloc_rows) and all(e[E_SEQ] > last for e in avail_rows))
    ck("d1.entry_numbers_rendered", all(e[E_NUM] == entry_number(e[E_SEQ]) for e in ents))
    ck("d1.issued_once", all(e[E_ASEQ] == 1 for e in alloc_rows) and all(e[E_ASEQ] == 0 for e in avail_rows),
       f"allocation_seq values {sorted({e[E_ASEQ] for e in ents})}")
    allnums = [e[E_NUM] for e in post.get("entries") or []]
    ck("d1.no_duplicate_numbers", len(allnums) == len(set(allnums)))

    # ---- per order (the run's own allocations: those not already in d1_pre; the baseline is checked unchanged separately)
    allocs = [a for a in post.get("allocations") or [] if str(a[A_CID]) == cid]
    pre_ids = {a[A_ID] for a in pre.get("allocations") or []}
    pre_l1 = sorted((a for a in pre.get("allocations") or [] if str(a[A_CID]) == cid), key=lambda a: a[A_ID])
    base_lines = {(num(r["order_gid"]), num(r["line_item_gid"])) for r in base["rows"]}
    ck("d1.baseline_allocations_unchanged", {(str(a[A_OID]), str(a[A_LID])) for a in pre_l1} == base_lines
       and len(pre_l1) == len(base_lines) and pre_l1 == sorted((a for a in allocs if a[A_ID] in pre_ids), key=lambda a: a[A_ID]),
       f"pre-run L1 allocations {len(pre_l1)}, baseline {len(base_lines)}")
    base_aids = {a[A_ID] for a in pre_l1}
    by_line = {}
    for a in allocs:
        if a[A_ID] not in base_aids:
            by_line.setdefault((str(a[A_OID]), str(a[A_LID])), []).append(a)
    ck("d1.one_allocation_per_line", all(len(v) == 1 for v in by_line.values()), "")
    exp_lines = {}
    for i, o in by_idx.items():
        li = (((o.get("lineItems") or {}).get("nodes")) or [{}])[0]
        exp_lines[(num(o["id"]), num(li.get("id", "")))] = i
    canary_oid = num(KNOWN_CANARY["order_gid"])
    ck("d1.known_canary_has_no_allocation", all(k[0] != canary_oid for k in by_line)
       and all(str(a[A_OID]) != canary_oid for a in post.get("allocations") or []), "")
    ck("d1.allocation_set_equals_orders", set(by_line) == set(exp_lines),
       f"allocations {len(by_line)}, orders {len(exp_lines)}, missing {len(set(exp_lines) - set(by_line))}, "
       f"unexpected {len(set(by_line) - set(exp_lines))}")
    held = {}
    for e in alloc_rows:
        held.setdefault(e[E_AID], []).append(e)
    qbad, cbad, mbad, zbad = [], [], [], []
    for key, al in by_line.items():
        a = al[0]
        i = exp_lines.get(key)
        q = rows[i]["qty"] if i else None
        hs = held.get(a[A_ID], [])
        if not (q is not None and a[A_QTY] == q and a[A_TGT] == q * a[A_PER] and a[A_HELD] == a[A_TGT] == len(hs) and a[A_ST] == "ALLOCATED"):
            qbad.append(a[A_NAME])
        s = sorted(e[E_SEQ] for e in hs)
        if s and s[-1] - s[0] + 1 != len(s):
            cbad.append(a[A_NAME])
        if any(str(e[E_OID]) != str(a[A_OID]) or str(e[E_LID]) != str(a[A_LID]) for e in hs):
            mbad.append(a[A_NAME])
        if a[A_UNIT] != 0 or a[A_TOTAL] != 0:
            zbad.append(a[A_NAME])
    ck("d1.exact_quantity_per_order", not qbad, f"{len(qbad)} wrong {qbad[:5]}")
    ck("d1.contiguous_blocks", not cbad, f"{cbad[:5]}")
    ck("d1.entries_match_allocation", not mbad and all(e[E_AID] in {a[A_ID] for a in allocs} for e in alloc_rows), f"{mbad[:5]}")
    ck("d1.zero_value_lines", not zbad, f"{zbad[:5]}")

    # ---- events
    pre_ev = {e[V_ID] for e in pre.get("events") or []}
    new_ev = [e for e in post.get("events") or [] if e[V_ID] not in pre_ev]
    l1_ev = [e for e in new_ev if str(e[V_CID]) == cid]
    ck("events.only_allocated", all(e[V_TYPE] == "ALLOCATED" for e in l1_ev), f"types {sorted({e[V_TYPE] for e in l1_ev})}")
    ck("events.count_equals_units", len(l1_ev) == units, f"{len(l1_ev)} vs {units}")
    issue = {}
    for e in l1_ev:
        issue.setdefault((e[V_SEQ], e[V_ASEQ]), []).append(e)
    ck("events.one_per_issue", all(len(v) == 1 for v in issue.values()), "")
    pool_ix = {(e[E_SEQ], e[E_ASEQ]): e for e in alloc_rows if e[E_AID] not in base_aids}
    em = [k for k, v in issue.items() if k not in pool_ix or v[0][V_AID] != pool_ix[k][E_AID] or v[0][V_NUM] != pool_ix[k][E_NUM]
          or str(v[0][V_OID]) != str(pool_ix[k][E_OID])]
    ck("events.match_pool", not em and set(issue) == set(pool_ix), f"{len(em)} mismatched")
    if inp.get("wl") is not None:
        known = {x.get("webhook_id") for x in inp["wl"]} | {x.get("run_id") for x in inp["wl"]}
        unk = [e[V_ID] for e in l1_ev if e[V_WH] not in known and e[V_RUN] not in known]
        ck("events.attributed_to_wl_invocations", not unk, f"{len(unk)} events with no matching Workers Logs invocation")
    ck("events.no_new_events_elsewhere", all(str(e[V_CID]) == cid for e in new_ev),
       f"{[e[V_ID] for e in new_ev if str(e[V_CID]) != cid][:5]}")

    # ---- integrity and untouched rows
    integ = post.get("integrity") or {}
    ck("integrity.all_zero", all(integ.get(k) == 0 for k in INTEGRITY_ZERO), json.dumps({k: integ.get(k) for k in INTEGRITY_ZERO}))
    pc = {c["competition_id"]: c for c in pre.get("competitions") or []}
    qc = {c["competition_id"]: c for c in post.get("competitions") or []}
    ck("untouched.competitions", set(pc) == set(qc) and all(pc[k] == qc[k] for k in pc), "")
    pe = {(e[E_CID], e[E_SEQ]): e for e in pre.get("entries") or [] if str(e[E_CID]) != cid}
    qe = {(e[E_CID], e[E_SEQ]): e for e in post.get("entries") or [] if str(e[E_CID]) != cid}
    ck("untouched.entries", pe == qe, f"{sum(1 for k in set(pe) | set(qe) if pe.get(k) != qe.get(k))} differ")
    pa = {a[A_ID]: a for a in pre.get("allocations") or []}
    qa = {a[A_ID]: a for a in post.get("allocations") or []}
    ck("untouched.allocations", all(qa.get(k) == v for k, v in pa.items()) and all(str(a[A_CID]) == cid for k, a in qa.items() if k not in pa), "")
    ck("untouched.events", all(e in (post.get("events") or []) for e in pre.get("events") or []), "")
    ck("untouched.put_summary", pre.get("put_summary") == post.get("put_summary"), "")

    # ---- cross-stage counts (each stage counted independently)
    stage = {"driver_confirmed": len(done), "shopify_orders": len(by_idx), "d1_allocations": len(by_line),
             "d1_units": len(alloc_rows) - bu, "events": len(l1_ev)}
    ck("cross.stage_counts", stage["driver_confirmed"] == stage["shopify_orders"] == stage["d1_allocations"]
       and stage["d1_units"] == stage["events"] == units, json.dumps(stage))
    ok = all(c["result"] == "PASS" for c in checks)
    return {"result": "PASS" if ok else "FAIL", "expected_orders": len(done), "expected_units": units, "stage_counts": stage,
            "baseline_units": bu, "excluded_known_canary": [o.get("id") for o in excluded],
            "excluded_baseline": sorted(o.get("id") for o in base_orders), "checks": checks}


# ---- recon.sql ---------------------------------------------------------------------------------------------------------------
def sql_checks():
    text = open(os.path.join(HERE, "recon.sql")).read()
    out = []
    for m in re.finditer(r"-- check: (\S+)\n(SELECT[^\n]*;)", text):
        out.append((m.group(1), m.group(2)))
    return out


def render(sql, params):
    def lit(k):
        v = params[k]
        if isinstance(v, int):
            return str(v)
        if not re.fullmatch(r"\d+", str(v)):
            raise ValueError(f"parameter {k} must be numeric")
        return "'" + str(v) + "'"
    return re.sub(r":(cid|start|last|cap|units|orders)\b", lambda m: lit(m.group(1)), sql)


def params_for(cid, units, orders, baseline_units=0, baseline_orders=0):
    """recon.sql parameters. units/orders are the run's own; the plan's baseline (run 2) is added, because recon.sql counts the
    whole L1 competition."""
    u, o = baseline_units + units, baseline_orders + orders
    return {"cid": str(cid), "start": START_NUMBER, "last": START_NUMBER + u - 1, "cap": CAPACITY, "units": u, "orders": o}


def run_sql(conn, params):
    """Run every recon.sql check (offline: an sqlite3 connection). Returns [{check, bad, result}]."""
    res = []
    for name, sql in sql_checks():
        bad = conn.execute(sql.rstrip(";"), params).fetchone()[0]
        res.append({"check": name, "bad": bad, "result": "PASS" if bad == 0 else "FAIL"})
    return res


def main(argv=None):
    p = argparse.ArgumentParser(prog="loadrecon.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--in", dest="inp", required=True); c.add_argument("--out", required=True)
    s = sub.add_parser("sql"); s.add_argument("--competition", required=True); s.add_argument("--units", type=int, required=True)
    s.add_argument("--orders", type=int, required=True); s.add_argument("--plan")
    a = p.parse_args(argv)
    if a.cmd == "sql":
        b = plan_baseline(read_json(a.plan)) if a.plan else {"units": 0, "rows": []}
        for name, sql in sql_checks():
            print(f"-- {name}\n{render(sql, params_for(a.competition, a.units, a.orders, b['units'], len(b['rows'])))}")
        return 0
    r = reconcile(json.load(open(a.inp)))
    write_json(a.out, r)
    print(f"RECON: {r['result']} ({sum(1 for c in r['checks'] if c['result'] == 'PASS')}/{len(r['checks'])} checks)")
    return 0 if r["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
