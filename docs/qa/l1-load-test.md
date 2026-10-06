# L1 allocator load test (design v2, approved 6 Oct 2026; staircase amended 6 Oct 2026)

Status: **Stage 1 (offline build + tests) only.** Nothing has run live. No product, draft, D1 row, order, webhook or Worker
change exists for L1. Harness: `/qa/l1-load-harness/` (README there lists every file and command).

## Question

How much sustained, genuine paid-order arrival can the finished allocator absorb before its backlog grows, measured through the
real path (Shopify order → `orders/paid` webhook → QA Worker → allocator → D1), without ever endangering the webhook subscriptions?
S4 (concurrency correctness) is closed and is not repeated.

## Verified constraints (6 Oct 2026, read-only)

| Fact | Source |
|---|---|
| Store plan Basic; each app has a 2,000-point bucket refilling at 100 points/s | read-only `throttleStatus`, allocator and Stress Driver apps |
| Allocator reads: order query actual 4 points; competition query actual 10 (twice on a first allocation) | read-only cost probe |
| "After 8 consecutive failures, the subscription is automatically deleted if it was configured using the Admin API." The allocator's 5 subscriptions are Admin-API created (24 Sep) | shopify.dev, Verify webhook deliveries; read-only `webhookSubscriptions` |
| Each D1 database processes queries one at a time | Cloudflare D1 limits |
| `wrangler tail` samples under high volume; Workers Logs keeps 100% (head sampling 1) | Cloudflare docs |
| A 100% draft discount gives a PAID, £0, transaction-free order that the allocator allocates (#1011, #1012) | Shopify + QA D1, read-only |

The 2–3.6 orders/s figure is a **hypothesis** from point costs, not a measured ceiling. L1 measures it.

## Shape (default path A → B → C → E; T and T2 never run by default)

- 750 orders, 1,650 entries, pool QAL1001–QAL3000 (350 headroom), seeded quantity mix 300×1, 225×2, 150×3, 60×5, 15×10, £0 via a
  100% draft discount, no customer/email/phone, `requiresShipping=false`.
- A: 50 orders at 0.5/s.
- B: staircase 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0 orders/s, 30 s per step (a step is complete when its quota, rate × 30 s, has been
  sent: 30, 45, 60, 75, 90, 105, 120 = 525 orders if every step passes). A step passes on a flat allocator bucket, no THROTTLED
  and completions ≥ 95% of arrivals. Escalation stops after a fully judged 4.0/s step; 4.5 and 5.0/s are never entered by the
  default profile (the 5/s hard ceiling remains as a global guard).
- C: every remaining order at r* (the last passing step): 175 orders if the whole staircase passes (50 + 525 + 175 = 750); more
  if the staircase stops earlier. E: drain.
- Reporting: a failing step gives the bracket [r*, r_fail). If every step through 4.0/s passes, the report says "Observed
  sustainable throughput ≥ 4.0 orders/s; upper stability boundary not bracketed by L1" and never calls 4.0/s a ceiling.
- No automatic retry or re-send of any draft. One-shot marker. T (read-only bucket clamp) and T2 (one induced failure) exist only
  behind their own approval phrases, are excluded from the default budget, and when T is approved its 40 orders must be budgeted
  before execution (C reduced to 135); the governor refuses otherwise. Total planned orders can never exceed 750.

## Safety envelope

| Level | Trigger | Action |
|---|---|---|
| WARNING | allocator bucket < 1,200 (or projected so within 10 s); THROTTLED retry; Worker wall > 3 s | no increase; drop to r* |
| LOAD STOP | first failed webhook delivery; unknown completion outcome; non-throttle completion error; bucket < 600; ≥ 3 THROTTLED in 10 s; stale telemetry | stop sending; window stays live; drain |
| SAFETY STOP | 2 consecutive or 3 total failures on one topic; the same webhook-id failing twice; failures 30 s after a LOAD STOP; subscription change; D1 change outside L1; non-plan allocation; production Worker; DRY_RUN change; manual stop; guard failure | restore to `DRY_RUN=true` at once, retried until verified |

Thresholds stay far below Shopify's 8. Drain: complete when D1 holds every completed draft's units; deadline 30 min after the last
order; no progress for 10 min → restore.

## Stages (each needs its own approval)

1. Offline build + tests (this stage).
2. Read-only rehearsals (`rehearse.py proxy`, `rehearse.py clamp`) and the single canary order.
3. Fixture: L1 product (connector), D1 registration (`register.sh`), 750 drafts (`stage.py`).
4. The one live run.

## Stage 2 canary — 6 Oct 2026

Order #1029 on the unregistered canary product: PAID, £0.00, £0 tax, no transaction; orders/create and orders/paid each delivered
once (HTTP 200, dry run); the allocator's sweep read it and left it out of scope; D1 unchanged; Klaviyo 0 events; no app activity on
the order. Evidence: `/qa/l1-load-harness/evidence/canary-2026-10-06/`.

Found by the canary (harness fix needed before Stage 3, not made): Shopify tag search is not exact. `draftOrders(query: "tag:QAL")`
matches the canary draft #D36 (tag QAL-CANARY), so `stage.py` would refuse to stage; `orders(query: "tag:qa-load")` returns the
canary order #1029, so the post-run export and reconciliation would count it as a non-plan order and fail L1. The canary order and
draft must be excluded explicitly (by id) in those checks.

Fixed offline (6 Oct 2026): `common.KNOWN_CANARY` holds the canary's exact draft and order GIDs. `stage.py` lists drafts tagged QAL
and qa-load and refuses on any of them except that exact draft; `shopsnapl.py` and `loadrecon.py` remove only that exact order from
the L1 population (reported as `excluded_known_canary`), fail on any other non-plan order or draft, and fail if the canary ever
holds an allocation. `canary.py` now refuses to create a second canary. Nothing is excluded by tag.

Stage 3 remains blocked until the owner confirms the side-effect checks for #1029 (staff email, UpPromote, Meta Events,
Flow / automations / accounting).

## Open design points before Stage 4

- Resolved (amendment of 6 Oct 2026): the staircase previously reached only 3.0/s (40 s steps, 500-order cap). It now judges every
  step through 4.0/s; above 4.0/s L1 reports a lower bound only.
- Launch risk found by analysis (not by L1): under sustained throttling the allocator returns 500, and Admin-API subscriptions are
  deleted after 8 consecutive failures. See `docs/open-items.md`.
