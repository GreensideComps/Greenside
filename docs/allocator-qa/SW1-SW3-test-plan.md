# Greenside Entry Allocator: sweep-recovery QA (SW1–SW3)

QA Worker `greenside-entry-allocator-qa`, commit `3facafc` (version
`ae18deed-8487-48ba-b363-39ed98f7c123`), QA D1 `greenside_entries_qa`.

Each test has two phases:

- **Phase A (dry-run):** proves the webhooks are acknowledged without writes
  and that the sweep detects the right fix.
- **Phase B (live):** proves the sweep makes the fix. It runs **only on
  explicit approval for that window**, behind the strict all-request gate,
  followed by a strict restore to `DRY_RUN="true"`.

Crons on QA: trailing sweep `*/15 * * * *`, deep sweep `20,50 * * * *`.

## How the sweep reports a detection

The sweep logs one `reconcile_would_change` line per order that needs a fix:

    { order_gid, reason, lines: [{ lineItemId, competitionId, action, target, held }] }

- `action`:
  - `CLAIM` means entries would be allocated;
  - `RELEASE` means entries would be released.
- **`target` is `null` when no allocation exists yet for the line.** Detection
  doesn't compute the entry target for a new order. The target (line quantity
  × entries per unit) is calculated during the actual claim, when the order is
  converged in a live run. When an allocation already exists, detection does
  report the numeric `target` and `held`.
- `reason` is the release reason derived from the order:
  - `CANCELLED` if the order is cancelled;
  - otherwise `REFUND` if it has refunds;
  - otherwise `ORDER_EDIT`.
- It's attached to every detection but only used when entries are released,
  so it has no effect on a `CLAIM`.

## SW1: recover a missed allocation

**Purpose:** a paid order whose webhooks arrived during dry-run gets its
entries later from the sweep.

### Phase A (dry-run)

Steps:
1. Create order #A: 2 × the QA competition product (15897614614902) at £0.01,
   marked paid manually (the C2a draft-order recipe).
2. Wait for the next trailing sweep.

Expected result:
- `orders/create` and `orders/paid` return 200 with `dry_run_webhook`.
- D1 is unchanged by the webhooks.
- The next trailing sweep logs `reconcile_would_change` for #A with:
  - `action: CLAIM`
  - `target: null` (no allocation exists yet; the target is calculated
    during the claim, not during dry-run detection)
  - `held: 0`
  - `reason: ORDER_EDIT`
- Sweep summary: `mismatched: 1`, `claimed: 0`, `errors: 0`.
- D1 is unchanged after the sweep: no allocation, event or other write.

**Result (25 Sep 2026): passed.**
- **Order:** #A = **#1014** (`gid://shopify/Order/13558545842550`), created
  22:11:37 UTC. PAID, `test:false`, manual gateway, line quantity 2.
- **Webhooks:** `orders/paid` (22:11:39) and `orders/create` (22:11:40) both
  returned 200 `dry_run_webhook` on version `ae18deed`.
- **Sweep:** trailing run `f06db733…` at 22:15:22 UTC logged
  `CLAIM / target null / held 0 / reason ORDER_EDIT` for line
  `39047250772342`, with `mismatched 1, claimed 0, errors 0`.
- **D1:** fingerprint `e1ba8582964bd3b9…` before, after the webhooks and after
  the sweep. Byte-identical each time.
- **Note:** the plan first stated `target 2, held 0 → ALLOCATE 2` for this
  step. That described the eventual claim, not the detection log format. It
  was corrected here, and the result is accepted as the code's actual
  behaviour, not a defect.

### Phase B (live, only on explicit approval)

Steps:
1. Strict gate to live.
2. No new webhooks; wait for the next sweep run.
3. Strict restore to dry-run.

Expected result:
- The sweep claims **exactly 2 entries for #1014: QAE1003 and QAE1004**
  (issue 2).
- **One allocation**, with `source` = `reconcile`.
- **Two `ALLOCATED` events**, `actor` = `system:reconcile`, `webhook_id` NULL.
- Events 11 → 13.
- Integrity checks 0.
- #1011, #1012 and #1013 rows byte-identical.
- Restore confirmed (`DRY_RUN="true"`).

**Result (25 Sep 2026): passed.** All times UTC.
- **Pre-live gate:** all checks passed before the switch.
  - Version `ae18deed` at 100% with `DRY_RUN="true"`.
  - #1014 PAID, quantity 2, not cancelled, no refunds.
  - QAE1003 and QAE1004 AVAILABLE.
  - D1 byte-identical to the Phase A baseline (fingerprint
    `e1ba8582964bd3b9…`: 11 events, 3 allocations, nothing for #1014).
  - No unexpected webhooks or Worker errors since Phase A.
  - Monitoring and heartbeat running.
- **Live version:** `6b5057f6-ba83-41ee-a41f-e8997333af07`
  (`3facafc`, `DRY_RUN=false`), deployed 22:35:01.
  - The strict gate passed at 22:35:50: 6 consecutive `dry_run:false`
    probes over 21s, each tail-confirmed on `6b5057f6`, and zero requests on
    any other version.
  - No webhooks and no errors while live.
- **Live sweep:** trailing run `44819012…`, scheduled 22:45:47, on
  `6b5057f6`.
  - Logged `reconcile_converged` for #1014: line `39047250772342`,
    `ALLOCATE`, claimed **exactly QAE1003 and QAE1004**, released none,
    `ELIGIBLE; target 2, held 0 -> 2`.
  - Summary: `mismatched 1, converged_orders 1, claimed 2, released 0,
    refused_not_open 0, unreadable 0, errors 0, aged_held 0`.
- **D1 after the sweep:** only the expected rows changed.
  - QAE1003 and QAE1004 are ALLOCATED to #1014 with `allocation_seq` 2
    (issue 2).
  - **1 allocation** for #1014: `source` = `reconcile`, ALLOCATED, target 2,
    held 2.
  - **2 `ALLOCATED` events** (12 and 13): `actor` = `system:reconcile`,
    `webhook_id` NULL.
  - **Events 11 → 13.**
  - Byte-identical: events 1–11, the #1011, #1012 and #1013 allocations,
    every other entry-number row, both competitions, and `webhook_delivery`.
  - Integrity checks: dup_events, audit_gaps, ledger_drift and orphans all 0.
  - Fingerprint `dbc4fc8e9de38d59…`.
- **Restored dry-run version:** `45971bda-6334-4b48-be6f-700a34b13746`
  (`3facafc`, `DRY_RUN="true"`), deployed 22:45:57. The strict restore gate
  passed at 22:46:41.
- **Rollout-transition probes:** the first two restore-gate probes (22:46:03
  and 22:46:08) were still served by the live version `6b5057f6`
  (`dry_run:false`) while the new version rolled out. The strict gate
  discarded them and reset its streak, as designed. This was expected
  rollout behaviour, not a test deviation. Every later probe and heartbeat
  was served by `45971bda` with `dry_run:true`.
- **Post-restore sweep:** deep run `234329f9…` at 22:50:47 on `45971bda`, in
  dry-run.
  - Reported `mismatched 0, claimed 0, released 0, errors 0`.
  - D1 unchanged (`dbc4fc8e…`).
- **Final `DRY_RUN="true"` verified:**
  - Cloudflare shows `45971bda` at 100% with `DRY_RUN="true"`.
  - `/health` returns `dry_run:true`.
  - The heartbeat returned `dry_run:true` 74 of 74 times after the restore.
- **Shopify:** #1014 unchanged (PAID, quantity 2, no refunds), and no other
  order was touched.

## SW2: recover a missed refund

**Purpose:** a refund acknowledged during dry-run is released later by the
sweep.

Phase A (dry-run):
- **Steps:** refund 1 of 2 on #1014 in Shopify admin (£0.01, restock).
- **Expected:** `refunds/create` returns 200 in dry-run; D1 unchanged; the
  sweep reports a release of 1.

Phase B (live, only on approval):
- **Steps:** strict gate; next sweep run; strict restore.
- **Expected:**
  - QAE1004 released (highest number first), reason `REFUND`, `detail_json`
    source `reconcile`;
  - allocation target 1, held 1;
  - events 13 → 15.

## SW3: recover a missed cancellation, idempotency and restore

**Purpose:** a cancellation acknowledged during dry-run is released later by
the sweep; further runs change nothing; the final state is clean.

Phase A (dry-run):
- **Steps:** cancel #1014 in Shopify admin (restock).
- **Expected:** `orders/cancelled` (and `refunds/create` if Shopify refunds the
  remaining £0.01) return 200 in dry-run; D1 unchanged; the sweep reports a
  release of 1.

Phase B (live, only on approval):
- **Steps:** strict gate; next sweep run; at least 3 further sweep runs while
  live; strict restore.
- **Expected:**
  - QAE1003 released, reason `CANCELLED`;
  - allocation `RELEASED`, target 0, held 0;
  - events 15 → 17;
  - the further runs add 0 events and 0 corrections;
  - final `DRY_RUN="true"` confirmed.

## Evidence collected for every test

- Tail captures and webhook deliveries.
- `reconcile_summary` and `reconcile_would_change` for every sweep run.
- D1 fingerprints before and after, with the exact row changes.
- Cloudflare active version, `DRY_RUN` and analytics.
- Strict-gate probe logs.
- Shopify state of the test order.
- A secrets scan of all captures.
