"""L1 load test: shared constants, the deterministic 750-order plan (run 2: its 742-row residual, amendment A1), the QAL allow-list
and evidence hashing.

Pure stdlib, no network. Every other L1 module imports its fixed facts from here so a reviewed change to one value is a change
in exactly one place. Design: docs/qa/l1-load-test.md (L1 v2, approved 6 Oct 2026)."""
import hashlib, json, os, re

# ---- fixture (L1) -------------------------------------------------------------------------------------------------------------
PREFIX = "QAL"
START_NUMBER = 1001
CAPACITY = 2000                         # QAL1001..QAL3000; 350 headroom makes over-allocation visible as extra numbers
PAD_WIDTH = len(str(START_NUMBER + CAPACITY - 1))
QUANTITY_MIX = ((1, 300), (2, 225), (3, 150), (5, 60), (10, 15))
N_ORDERS = sum(n for _, n in QUANTITY_MIX)                    # 750
UNITS = sum(q * n for q, n in QUANTITY_MIX)                   # 1650
EXPECTED_LAST_SEQ = START_NUMBER + UNITS - 1                  # 2650
PLAN_SEED = "greenside-L1-QAL-2026-10"
ORDER_TAG = "qa-load"
PRODUCT_TITLE = "QA — Allocator LOAD Test L1 (QA ONLY — NOT FOR SALE)"
PRODUCT_HANDLE = "qa-allocator-load-test-l1"
PRODUCT_TAGS = ("qa-only", "qa-load", PREFIX)
UNIT_PRICE = "1.00"                     # line price; every draft carries a 100% discount, so every order total is 0.00
DISCOUNT_TITLE = "QA L1 load test (100%, no charge)"
CANARY_TITLE = "QA CANARY L1-C (QA ONLY — NOT FOR SALE)"
CANARY_TAG = "QAL-CANARY"

# ---- earlier QA fixtures: never consumable by any L1 tool -------------------------------------------------------------------
RETIRED_PREFIXES = frozenset({"QAE", "QAF", "QAG", "QAH", "QAI", "QAJ", "PUT"})
RETIRED_COMPETITIONS = {                # QA D1, read-only listing of 6 Oct 2026
    "900001": "PUT", "15897614614902": "QAE", "15902004674934": "QAF", "15902724817270": "QAG",
    "15903096668534": "QAH", "15905304379766": "QAI", "15915106926966": "QAJ",
}

# ---- the ONE known Stage 2 canary (6 Oct 2026; evidence/canary-2026-10-06). Shopify tag search is not exact, so the canary
# draft matches "tag:QAL" and its order matches "tag:qa-load". It is excluded from L1 by these exact IDs only, never by tag; any
# other non-plan draft or order (including a second canary) still fails the checks.
KNOWN_CANARY = {
    "draft_gid": "gid://shopify/DraftOrder/1614955151734",
    "order_gid": "gid://shopify/Order/13599260639606",
    "order_name": "#1029",
    "product_gid": "gid://shopify/Product/15916653642102",
}


def is_known_canary_draft(gid):
    return gid == KNOWN_CANARY["draft_gid"]


def is_known_canary_order(gid):
    return gid == KNOWN_CANARY["order_gid"]


def split_known_canary_orders(orders):
    """(population, excluded): the known canary order removed by its exact GID; everything else stays in the population."""
    pop = [o for o in orders if not is_known_canary_order(o.get("id"))]
    return pop, [o for o in orders if is_known_canary_order(o.get("id"))]


# ---- L1 run 2: the residual fixture (amendment A1, 7 Oct 2026) ----------------------------------------------------------------
# Stage 4 (7 Oct 2026) completed plan rows 1-8 of the bound run-1 plan, then stopped. Run 2 completes the remaining 742 OPEN drafts
# of that SAME plan (rows 9-750: unchanged indexes, quantities, tags and draft GIDs) against the QAL pool as Stage 4 left it.
# The 8 Stage 4 orders and their 13 entries (QAL1001-QAL1013) are the run-2 BASELINE. They are pinned here by exact ID, must be
# found unchanged, and are never re-sent; like the canary they are excluded by exact ID only, never by tag.
RUN1_PLAN_SHA = "6fa7442b9089ea384fbf3eb478b50c49f37abaabfa4787f5552a068a496cc1f5"   # evidence/stage3-2026-10-07/plan-bound.json
L1_COMPETITION_ID = "15918227030390"
RUN2_FIRST_INDEX = 9
RUN2_ROWS_SHA = "11e9dce30abaf44c48c73794aaf4891e7f0051b8972fb0f56d3280ba24980d02"   # sha256(canonical(run-1 rows 9..750))
RUN2_QUANTITY_MIX = ((1, 294), (2, 224), (3, 150), (5, 59), (10, 15))
RUN2_N_ORDERS = sum(n for _, n in RUN2_QUANTITY_MIX)                                  # 742
RUN2_UNITS = sum(q * n for q, n in RUN2_QUANTITY_MIX)                                 # 1637
# (plan index, qty, draft GID, order GID, order name, line item GID): evidence/stage4-2026-10-07 (recon-input.json.gz)
RUN2_BASELINE_ROWS = (
    (1, 2, "gid://shopify/DraftOrder/1615108800886", "gid://shopify/Order/13602149138806", "#1030", "gid://shopify/LineItem/39144780104054"),
    (2, 1, "gid://shopify/DraftOrder/1615108833654", "gid://shopify/Order/13602149204342", "#1031", "gid://shopify/LineItem/39144780136822"),
    (3, 5, "gid://shopify/DraftOrder/1615108866422", "gid://shopify/Order/13602149335414", "#1032", "gid://shopify/LineItem/39144780431734"),
    (4, 1, "gid://shopify/DraftOrder/1615108899190", "gid://shopify/Order/13602149499254", "#1033", "gid://shopify/LineItem/39144780759414"),
    (5, 1, "gid://shopify/DraftOrder/1615108931958", "gid://shopify/Order/13602149597558", "#1034", "gid://shopify/LineItem/39144781250934"),
    (6, 1, "gid://shopify/DraftOrder/1615108964726", "gid://shopify/Order/13602149794166", "#1035", "gid://shopify/LineItem/39144781414774"),
    (7, 1, "gid://shopify/DraftOrder/1615108997494", "gid://shopify/Order/13602150023542", "#1036", "gid://shopify/LineItem/39144781742454"),
    (8, 1, "gid://shopify/DraftOrder/1615109030262", "gid://shopify/Order/13602150121846", "#1037", "gid://shopify/LineItem/39144781971830"),
)
RUN2_BASELINE_UNITS = sum(r[1] for r in RUN2_BASELINE_ROWS)                          # 13: QAL1001..QAL1013
assert RUN2_N_ORDERS == 742 and RUN2_UNITS == 1637 and RUN2_BASELINE_UNITS == 13 and len(RUN2_BASELINE_ROWS) == RUN2_FIRST_INDEX - 1


def run2_baseline():
    """The baseline block a run-2 plan must carry, exactly."""
    return {"units": RUN2_BASELINE_UNITS,
            "rows": [{"index": i, "qty": q, "tag": f"QAL-{i:04d}-q{q}", "draft_id": d, "order_gid": o, "order_name": n,
                      "line_item_gid": li} for i, q, d, o, n, li in RUN2_BASELINE_ROWS]}


def plan_baseline(plan):
    """The plan's baseline block ({units, rows}); empty (0 units, no rows) for a run-1 plan."""
    b = plan.get("baseline") or {}
    return {"units": b.get("units", 0), "rows": list(b.get("rows") or [])}


def baseline_order_gids(plan):
    return {r["order_gid"] for r in plan_baseline(plan)["rows"]}


def baseline_draft_gids(plan):
    return {r["draft_id"] for r in plan_baseline(plan)["rows"]}


def split_baseline_orders(orders, plan):
    """(population, baseline): the plan's baseline orders removed by their exact GIDs; everything else stays in the population."""
    gids = baseline_order_gids(plan)
    return [o for o in orders if o.get("id") not in gids], [o for o in orders if o.get("id") in gids]


# ---- approval phrases (each stage needs its own explicit approval) -------------------------------------------------------------
CONFIRM_LIVE = f"LOAD-{PREFIX}-{N_ORDERS}-ORDERS-{UNITS}-ENTRIES"
CONFIRM_LIVE_RUN2 = f"LOAD-{PREFIX}-{RUN2_N_ORDERS}-ORDERS-{RUN2_UNITS}-ENTRIES"
APPROVE_T = "APPROVE-L1-T-THROTTLE-PROBE"
APPROVE_T2 = "APPROVE-L1-T2-SINGLE-FAILURE-PROBE"
CONFIRM_STAGE = f"STAGE-{PREFIX}-{N_ORDERS}-DRAFTS"
CONFIRM_CANARY = f"CANARY-{PREFIX}-L1-C-1-ORDER"
CONFIRM_REGISTER = f"REGISTER-{PREFIX}-{CAPACITY}-ENTRIES"
CONFIRM_REHEARSE = "REHEARSE-L1-READONLY-0-MUTATIONS"

# ---- load profile (L1 v2) -----------------------------------------------------------------------------------------------------
HARD_CEILING_PER_S = 5.0
PHASE_A_ORDERS, PHASE_A_RATE = 50, 0.5
# Default first-run staircase (amendment of 6 Oct 2026): 30 s per step at 1.0..4.0/s by 0.5, escalation stops after a fully
# judged 4.0/s step. 4.5 and 5.0/s are never entered by the approved profile; HARD_CEILING_PER_S stays as the global guard.
STAIRCASE = (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0)
STEP_SECONDS = 30.0
STEP_QUOTAS = tuple(int(round(r * STEP_SECONDS)) for r in STAIRCASE)       # 30, 45, 60, 75, 90, 105, 120
PHASE_B_MAX_ORDERS = sum(STEP_QUOTAS)                                        # 525: a full staircase, never cut short
PHASE_T_ORDERS, PHASE_T_RATE = 40, 0.5
assert STAIRCASE[-1] <= HARD_CEILING_PER_S and PHASE_B_MAX_ORDERS == 525


def population_budget(n_orders=None, t_budget=0):
    """The run's order budget, fixed before execution. T is excluded unless explicitly budgeted (t_budget = PHASE_T_ORDERS);
    C gets everything else. Raises Refused if the phases cannot fit in the plan."""
    n = N_ORDERS if n_orders is None else n_orders
    if t_budget not in (0, PHASE_T_ORDERS):
        raise Refused(f"T budget must be 0 (T not approved) or exactly {PHASE_T_ORDERS}")
    c_min = n - PHASE_A_ORDERS - PHASE_B_MAX_ORDERS - t_budget
    if n > N_ORDERS or c_min < 0:
        raise Refused(f"population {n} cannot hold A {PHASE_A_ORDERS} + B {PHASE_B_MAX_ORDERS} + T {t_budget} (max {N_ORDERS})")
    return {"total": n, "A": PHASE_A_ORDERS, "B_max": PHASE_B_MAX_ORDERS, "T": t_budget, "C_min": c_min}


def stability_statement(bracket):
    """The report wording for the stability point. Never claims an allocator ceiling."""
    if not bracket:
        return "Stability point not determined (the staircase did not run)."
    lo, hi = bracket.get("low"), bracket.get("high")
    if bracket.get("bracketed"):
        if lo is None:
            return f"Stability point below {hi} orders/s: even the {hi}/s baseline did not stay stable."
        return f"Stability point in [{lo}, {hi}) orders/s: {lo}/s was sustained, {hi}/s was not."
    return (f"Observed sustainable throughput >= {lo} orders/s; upper stability boundary not bracketed by L1 "
            f"(L1 did not test above {lo}/s; this is not an allocator ceiling).")

# ---- safety envelope (L1 v2 section 3) ----------------------------------------------------------------------------------------
BUCKET_WARN = 1200          # allocator app bucket below this: WARNING (no increase; drop to r*)
BUCKET_LOAD_STOP = 600      # below this outside T: LOAD STOP
WALL_WARN_MS = 3000         # Worker wall time above this: WARNING
SHOPIFY_TIMEOUT_MS = 5000   # Shopify counts no response within 5 s as a failed delivery
THROTTLE_LOAD_STOP_N, THROTTLE_LOAD_STOP_WINDOW_S = 3, 10.0
SAFETY_CONSECUTIVE = 2      # consecutive failed deliveries on one topic
SAFETY_TOTAL = 3            # failed deliveries in total on one topic
FAIL_AFTER_LOAD_STOP_S = 30.0
SHOPIFY_DELETE_AFTER = 8    # verified: 8 consecutive failures delete an Admin-API subscription
DRAIN_DEADLINE_S = 30 * 60
NO_PROGRESS_S = 10 * 60
D1_COUNT_EVERY_S = 30
ALLOWLIST_EVERY_S = 300
SUBSCRIPTION_EVERY_S = 30
SAMPLER_EVERY_S = 2.0
CLAMP_MAX_WINDOW_S = 1.5
CLAMP_T_WINDOWS = 2
CLAMP_T2_MAX_WINDOW_S = 3.0
WEBHOOK_TOPICS = ("orders/create", "orders/paid", "orders/cancelled", "orders/edited", "refunds/create")


class Refused(Exception):
    """A gate refused before anything was sent."""


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(s):
    return hashlib.sha256(s.encode()).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def entry_number(seq):
    return f"{PREFIX}{seq:0{PAD_WIDTH}d}"


def order_tag(index, qty):
    return f"{PREFIX}-{index:04d}-q{qty}"


TAG_RE = re.compile(r"^QAL-(\d{4})-q(\d+)$")


def parse_tag(tags):
    """(index, qty) from an order's tags, or None. Exactly one QAL-NNNN-qQ tag is required."""
    hits = [TAG_RE.match(t.strip()) for t in (tags or [])]
    hits = [m for m in hits if m]
    if len(hits) != 1:
        return None
    return int(hits[0].group(1)), int(hits[0].group(2))


def plan_rows():
    """The deterministic plan: 750 rows (index 1..750, qty) in a seeded order. The order is defined by sha256(seed:k) so it is
    identical on any Python version or machine (no dependence on random's algorithm)."""
    qtys = [q for q, n in QUANTITY_MIX for _ in range(n)]
    keyed = sorted(range(len(qtys)), key=lambda k: hashlib.sha256(f"{PLAN_SEED}:{k}".encode()).hexdigest())
    return [{"index": i + 1, "qty": qtys[k], "tag": order_tag(i + 1, qtys[k])} for i, k in enumerate(keyed)]


def plan_template():
    rows = plan_rows()
    return {"kind": "L1-plan-template", "prefix": PREFIX, "start_number": START_NUMBER, "capacity": CAPACITY,
            "orders": len(rows), "units": sum(r["qty"] for r in rows), "seed": PLAN_SEED, "rows": rows}


def plan_hash(plan):
    return sha256_text(canonical(plan))


def plan_run(plan):
    """1 (the original 750-order run) or 2 (the residual 742-order run, amendment A1). Anything else is invalid."""
    return plan.get("run", 1)


def _profile(run):
    if run == 2:
        return {"orders": RUN2_N_ORDERS, "units": RUN2_UNITS, "mix": RUN2_QUANTITY_MIX, "first": RUN2_FIRST_INDEX,
                "confirm": CONFIRM_LIVE_RUN2}
    return {"orders": N_ORDERS, "units": UNITS, "mix": QUANTITY_MIX, "first": 1, "confirm": CONFIRM_LIVE}


def expected_orders(plan):
    """The number of plan drafts the run must find OPEN and may complete: fixed by the run, never by the plan's own fields."""
    return _profile(plan_run(plan))["orders"]


def confirm_live(plan):
    """The exact live confirmation phrase for this plan's run."""
    return _profile(plan_run(plan))["confirm"]


def derive_run2_plan(run1):
    """The run-2 plan: the bound run-1 plan's rows 9..750 unchanged, plus the exact Stage 4 baseline. Offline, deterministic.
    Raises Refused unless run1 is exactly the approved bound run-1 plan and its rows 1..8 are the pinned Stage 4 drafts."""
    if plan_run(run1) != 1 or validate_plan(run1, bound=True):
        raise Refused("the source is not a valid bound run-1 plan")
    if plan_hash(run1) != RUN1_PLAN_SHA:
        raise Refused("the source plan is not the approved run-1 plan (sha256 differs)")
    head = [(r["index"], r["qty"], r["draft_id"]) for r in run1["rows"][:RUN2_FIRST_INDEX - 1]]
    if head != [(i, q, d) for i, q, d, _, _, _ in RUN2_BASELINE_ROWS]:
        raise Refused("run-1 rows 1..8 are not the pinned Stage 4 drafts")
    plan = {k: v for k, v in run1.items() if k != "rows"}
    plan.update(kind="L1-plan-run2", run=2, derived_from=RUN1_PLAN_SHA, orders=RUN2_N_ORDERS, units=RUN2_UNITS,
                baseline=run2_baseline(), rows=[dict(r) for r in run1["rows"][RUN2_FIRST_INDEX - 1:]])
    probs = validate_plan(plan, bound=True)
    if probs:
        raise Refused("derived run-2 plan invalid: " + "; ".join(probs[:5]))
    return plan


def validate_plan(plan, bound=False):
    """Problems with a plan (template or bound; run 1 or run 2). Empty list = valid."""
    p = []
    rows = plan.get("rows") or []
    run = plan_run(plan)
    if run not in (1, 2):
        return [f"unknown run {run!r}"]
    prof = _profile(run)
    if run == 1 and "baseline" in plan:
        p.append("a run-1 plan carries no baseline")
    if run == 2:
        if not bound:
            p.append("a run-2 plan is always bound")
        if plan.get("derived_from") != RUN1_PLAN_SHA:
            p.append("run-2 plan is not derived from the approved run-1 plan")
        if str(plan.get("competition_id")) != L1_COMPETITION_ID:
            p.append(f"run-2 plan is not for competition {L1_COMPETITION_ID}")
        if plan.get("baseline") != run2_baseline():
            p.append("run-2 baseline is not exactly the 8 Stage 4 orders (QAL1001..QAL1013)")
        if sha256_text(canonical(rows)) != RUN2_ROWS_SHA:
            p.append("run-2 rows are not exactly run-1 rows 9..750")
        if plan.get("orders") != RUN2_N_ORDERS or plan.get("units") != RUN2_UNITS:
            p.append(f"run-2 header {plan.get('orders')} orders / {plan.get('units')} units")
    if plan.get("prefix") != PREFIX:
        p.append(f"prefix {plan.get('prefix')!r} is not {PREFIX}")
    if plan.get("prefix") in RETIRED_PREFIXES:
        p.append("retired prefix")
    if len(rows) != prof["orders"]:
        p.append(f"{len(rows)} rows, expected {prof['orders']}")
    if sum(r.get("qty", 0) for r in rows) != prof["units"]:
        p.append(f"units {sum(r.get('qty', 0) for r in rows)}, expected {prof['units']}")
    mix = {}
    for r in rows:
        mix[r.get("qty")] = mix.get(r.get("qty"), 0) + 1
    if mix != dict(prof["mix"]):
        p.append(f"quantity mix {sorted(mix.items())} != {list(prof['mix'])}")
    if [r.get("index") for r in rows] != list(range(prof["first"], prof["first"] + prof["orders"])):
        p.append(f"indexes are not exactly {prof['first']}..{prof['first'] + prof['orders'] - 1} in order")
    for r in rows:
        if r.get("tag") != order_tag(r.get("index", 0), r.get("qty", 0)):
            p.append(f"row {r.get('index')}: tag {r.get('tag')!r} does not encode its index and quantity")
            break
    if bound:
        cid = str(plan.get("competition_id") or "")
        if not cid.isdigit():
            p.append("bound plan has no numeric competition_id")
        if cid in RETIRED_COMPETITIONS:
            p.append(f"competition {cid} is a retired fixture ({RETIRED_COMPETITIONS[cid]})")
        ids = [r.get("draft_id") for r in rows]
        if any(not (isinstance(x, str) and x.startswith("gid://shopify/DraftOrder/")) for x in ids):
            p.append("a bound row has no DraftOrder GID")
        if len(set(ids)) != len(ids):
            p.append("duplicate draft GIDs")
    return p


def allowed_draft(plan, draft_id):
    """True only for a draft GID bound in this (valid, bound) plan."""
    return any(r.get("draft_id") == draft_id for r in plan.get("rows") or [])


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(canonical(obj) + "\n")
    os.replace(tmp, path)


def read_json(path):
    with open(path) as f:
        return json.load(f)


def scrub(text, secrets):
    """Replace any known secret value by [REDACTED]; used on every evidence line before it is written."""
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, "[REDACTED]")
    return text
