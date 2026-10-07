# L1 allocator load test (design v2, approved 6 Oct 2026; staircase amended 6 Oct 2026)

Status: Stage 1 done (offline). Stage 2 done (canary #1029, 6 Oct 2026; side-effect checks confirmed by the owner on 7 Oct 2026).
Stage 4 wiring done offline (7 Oct 2026, below). **Stage 3 fixture done 7 Oct 2026**: product 15918227030390 (variant
58912245252470), QA D1 competition 15918227030390 QAL1001-QAL3000 AVAILABLE, 750 OPEN £0 drafts #D37-#D786, bound plan sha256
6fa7442b…; evidence `/qa/l1-load-harness/evidence/stage3-2026-10-07/`. **Stage 4 ran once on 7 Oct 2026 (13:01-13:16 BST): LOAD STOP after 8 of 750 orders in
baseline phase A, L1 FAIL** — an orders/create delivery returned HTTP 500 on `UNIQUE(allocation.order_id, line_item_id)` because the
concurrent orders/paid delivery for the same order had allocated first (Shopify's retry then returned 200). Drained (13 entries,
QAL1001-QAL1013, exact), restored (004e753f, DRY_RUN=true), post-check PASSED. Throughput not measured (staircase never entered).
Evidence `/qa/l1-load-harness/evidence/stage4-2026-10-07/`.
Harness: `/qa/l1-load-harness/` (README there lists every file and command).

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

Side-effect checks for #1029, confirmed by the owner on 7 Oct 2026: staff email clean (but the info@ mailbox has received no
staff order email since #1004: MUST FIX BEFORE LAUNCH, not an L1 blocker); Shopify app push SIDE EFFECT, ACCEPTED, including up
to 750 pushes in the live run; Klaviyo clean (0 events); UpPromote clean; Meta clean (channel not connected); accounting: none
configured; Shopify Flow installed with no active workflows; analytics and the weekly summary include QA orders: ACCEPTED.

## Stage 4 wiring (offline, 7 Oct 2026)

Found before any live step: the committed guard never received the QA Worker or Workers Logs inputs (`guardl.py collect` was called
without them), so "production Worker present", "DRY_RUN changed" and Workers Logs failure confirmation were inactive during a run,
and nothing produced the state files `load.py live` and the guard read. Added, offline only, with no change to the governor, the
thresholds, the staircase, the failwatch rules, `load.py`, `sampler.py` or any B3 script (pinned by sha256 in the tests):

- `livewin.py` (read-only): a QA Worker poller (the same GETs and production-Worker rule as `fire.py check_live_gates`) and a
  Workers Logs poller (`b3obs/obsq.py` windows, contiguous, 60 s behind now); writers and checks for every state file.
- `guardl.py`: reads both poller files. While live (from the gate until a restore is requested): no fresh good Worker reading for
  30 s, a production Worker, a missing or unexpected `DRY_RUN`, or a version other than the gate-passed one is a SAFETY STOP; every
  Workers Logs window goes through the existing failwatch (failures count; an incomplete window is a LOAD STOP); a coverage gap, a
  malformed window, an unknown start or coverage older than 150 s is a LOAD STOP.
- `window.sh`: the Stage 4 runbook over the unchanged B3 `gate.sh` / strict gate / `restore.sh` / `postcheck.sh`.

New checks, new numbers (none of the existing thresholds changed): Worker poll 10 s, Worker stale 30 s; Workers Logs poll 30 s,
lag 60 s, stale 150 s. Known limitation: under the strict Workers Logs rule an incomplete window (for example an invocation group
missing its record) is a LOAD STOP, which ends the staircase early; it never passes silently.

## Amendment A1: run 2 on the residual fixture (offline, 7 Oct 2026; approved for offline work only)

Stage 4 stopped after rows 1-8 (#1030-#1037, 13 entries QAL1001-QAL1013). The allocator race it found is fixed (610e189,
QA-verified 7 Oct). Run 2 reuses the fixture as it stands; no restaging, no cleanup:

- Plan: `load.py plan-run2` derives it offline from the approved run-1 plan (sha256 `6fa7442b…`): rows 9-750 unchanged (742
  orders, 1,637 units, mix 294×1, 224×2, 150×3, 59×5, 15×10), `"run": 2`, plus a baseline block with the 8 Stage 4 orders.
  `validate_plan` accepts it only if the rows hash, the baseline and the source sha equal the constants pinned in `common.py`.
  Run-2 plan sha256 `c6438b909b3037ff6e685b194d273b437f32aa19fee37a7374eeeb72bf1791ea`; phrase `LOAD-QAL-742-ORDERS-1637-ENTRIES`.
- D1 reference: instead of a fresh pool, exactly the 8 baseline allocations holding QAL1001-QAL1013 once each with 13 ALLOCATED
  events, and QAL1014-QAL3000 AVAILABLE and never issued. The real Stage 4 `d1-final.json` passes this rule (tested).
- Governor budget = the plan's rows: A 50, B up to 525, C 167 (was 175). Staircase, thresholds and failwatch unchanged.
- Guard: drained when the L1 pool holds baseline (13) + completed units; a missing baseline never drains (the deadline restores).
- Reconciliation (`loadrecon.py`, `recon.sql` parameters, `shopsnapl.py`): the baseline orders are excluded by exact GID only, must
  be present, their drafts COMPLETED and their allocations unchanged; the run's numbers must be exactly QAL1014..QAL(1013 + units).
  The post-run draft read (by exact ID, as in Stage 4) must therefore cover all 750 run-1 draft GIDs (the 742 plan rows and the 8
  baseline drafts); a listing without the baseline drafts fails `shopify.baseline_drafts_completed`.
- Allocator pin: 610e189 (the race fix) in `livewin.py`, B3 `gate.sh`, `restore.sh`, `goliveb.sh` and both READMEs. `gate.sh` and
  `restore.sh` differ from ef98661 only by that pin (tested).
- Known: `d1.zero_value_lines` / recon.sql `zero_value` will FAIL again in run 2, as in Stage 4 (the allocator records
  `line_total_minor` before the 100% discount; open item). The check is unchanged; it is reported, not suppressed.

## Open design points before Stage 4

- Resolved (amendment of 6 Oct 2026): the staircase previously reached only 3.0/s (40 s steps, 500-order cap). It now judges every
  step through 4.0/s; above 4.0/s L1 reports a lower bound only.
- Launch risk found by analysis (not by L1): under sustained throttling the allocator returns 500, and Admin-API subscriptions are
  deleted after 8 consecutive failures. See `docs/open-items.md`.
