# L1 allocator load test (design v2, approved 6 Oct 2026)

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
- A: 50 orders at 0.5/s. B: staircase 1.0 → 5.0/s in 0.5 steps, 40 s each, at most 500 orders; a step passes on a flat allocator
  bucket, no THROTTLED and completions ≥ 95% of arrivals. C: the rest at r* (last passing step). E: drain.
- Hard ceiling 5/s. No automatic retry or re-send of any draft. One-shot marker. T (read-only bucket clamp) and T2 (one induced
  failure) exist only behind their own approval phrases.

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

## Open design points before Stage 4

- With 40 s steps and the 500-order B cap, the staircase can fully judge steps only up to 3.0/s (cumulative 400 orders); the
  3.5/s step is cut by the cap and recorded INCOMPLETE. If capacity is above 3.0/s the stability point stays unbracketed
  (reported as "≥ 3.0/s"). Raising the B cap to about 650 orders, or using 30 s steps, would reach 4.0/s; this needs a decision.
- Launch risk found by analysis (not by L1): under sustained throttling the allocator returns 500, and Admin-API subscriptions are
  deleted after 8 consecutive failures. See `docs/open-items.md`.
