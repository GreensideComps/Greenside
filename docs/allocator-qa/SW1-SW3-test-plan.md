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
  - QAE1004 released (highest number first), reason `REFUND`. QAE1003
    stays allocated.
  - Two events: `RELEASED` (reason `REFUND`), then `RETURNED_TO_POOL`
    (`detail_json` `{"reason":"REFUND"}`).
  - The reconciliation source shows in each event as `actor` =
    `system:reconcile`, the sweep's `run_id` and `webhook_id` NULL.
    `detail_json` does **not** record a source. Where it applies, it holds
    the release reason, in the same format as a webhook release.
  - Allocation target 1, held 1.
  - Events 13 → 15.
- **Correction:** the plan first said `detail_json` would contain source
  `reconcile`. That was a documentation error, not a code defect: the code has
  no path that writes a source into `detail_json`.

**Phase B result (26 Sep 2026): passed.** All times UTC.
- **Live version:** `37f83f57-7bb1-4a86-b9f4-27382c2e6054`
  (`3facafc`, `DRY_RUN=false`), deployed 09:35:01.
  - The strict gate passed at 09:35:49: 6 consecutive `dry_run:false`
    probes over 21s, each tail-confirmed on `37f83f57`, and zero requests on
    any other version.
  - No webhooks and no errors while live.
- **Live sweep:** trailing run `23098a12…`, scheduled 09:45:24, on
  `37f83f57`.
  - Logged `reconcile_converged` for #1014, reason `REFUND`: line
    `39047250772342`, `RELEASE`, released **QAE1004**, claimed none,
    `CONVERGE_ONLY; target 1, held 2 -> 1`.
  - Summary: `mismatched 1, converged_orders 1, claimed 0, released 1,
    refused_not_open 0, unreadable 0, errors 0, aged_held 0`.
- **D1 after the sweep:** only the expected rows changed.
  - **QAE1004** was released and **returned to the available pool**
    (AVAILABLE, `release_reason` `REFUND`).
  - **QAE1003 remained allocated** to #1014 (issue 2).
  - The #1014 allocation (`source` `reconcile`) went from **target 2 → 1**
    and **held 2 → 1**.
  - **Events 13 → 15**, both with `actor` = `system:reconcile`, run
    `23098a12…` and `webhook_id` NULL:
    - Event 14: QAE1004 `RELEASED`, reason `REFUND`, `detail_json` `{}`.
    - Event 15: QAE1004 `RETURNED_TO_POOL`, `detail_json`
      `{"reason":"REFUND"}`.
  - Byte-identical: events 1–13, the #1011, #1012 and #1013 allocations,
    every other entry-number row, both competitions, and `webhook_delivery`.
  - Integrity checks: dup_events, audit_gaps, ledger_drift and orphans all 0.
  - Fingerprint `789e4c321dc0d8f8…`.
- **Restored dry-run version:** `f30cda23-dec0-490c-8a60-0b871d569b49`
  (`3facafc`, `DRY_RUN="true"`), deployed 09:45:32. The strict restore gate
  passed at 09:46:10.
- **Rollout-transition probes:** the first restore-gate probe (09:45:37) and
  heartbeat hb-343 (09:45:33) were still served by the live version
  `37f83f57` while the new version rolled out.
  - The strict gate discarded the probe and reset its streak, as designed.
  - This was expected rollout behaviour, not a test deviation.
  - No webhook or sweep ran in that period.
- **Post-restore sweep:** deep run `492368f6…` at 09:50:24 on `f30cda23`,
  in dry-run.
  - Reported `mismatched 0, claimed 0, released 0, errors 0`.
  - D1 unchanged (`789e4c32…`).
- **Final `DRY_RUN="true"` verified:**
  - Cloudflare shows `f30cda23` at 100% with `DRY_RUN="true"`.
  - `/health` returns `dry_run:true`.
  - The heartbeat returned `dry_run:true` 74 of 74 times after the restore.
- **Shopify:** #1014 unchanged (PARTIALLY_REFUNDED, current quantity 1,
  refunded £0.01), and no other order changed.

## SW3: recover a missed cancellation, idempotency and restore

**Purpose:** a cancellation acknowledged during dry-run is released later by
the sweep; further runs change nothing; the final state is clean.

Phase A (dry-run):
- **Steps:** cancel #1014 in Shopify admin (restock).
- **Expected:** `orders/cancelled` (and `refunds/create` if Shopify refunds the
  remaining £0.01) return 200 in dry-run; D1 unchanged; the sweep reports a
  release of 1.

**Phase A result (26 Sep 2026): passed.** All times UTC.
- **Pre-state:**
  - Worker `f30cda23` (`3facafc`) at 100% with `DRY_RUN="true"`.
  - #1014 PARTIALLY_REFUNDED, current quantity 1.
  - QAE1003 ALLOCATED and QAE1004 AVAILABLE; allocation target 1 / held 1.
  - 15 events, 4 allocations, integrity checks all 0.
  - D1 byte-identical to the SW2 Phase B final state (`789e4c32…`).
- **Cancellation:** #1014 was cancelled in Shopify Admin at 10:04:23 with
  restock.
  - Shopify refunded the remaining £0.01 (manual, restocked).
  - #1014 is now cancelled, REFUNDED and closed, current quantity 0.
- **Webhooks:** both returned 200 `dry_run_webhook` on `f30cda23`, and no
  other webhook arrived:
  - `refunds/create` at 10:04:24;
  - `orders/cancelled` at 10:04:25.
- **D1 after the webhooks:** byte-identical (`789e4c32…`).
- **Sweep:** trailing run `2ce6d31e…`, scheduled 10:15:24, on `f30cda23`, in
  report mode.
  - Logged `reconcile_would_change` for #1014: reason `CANCELLED`, line
    `39047250772342`, `RELEASE`, target 0, held 1.
  - Summary: `mismatched 1, converged_orders 0, claimed 0, released 0,
    unreadable 0, errors 0`.
- **D1 after the sweep:** byte-identical (`789e4c32…`).
  - QAE1003 is still ALLOCATED to #1014, and the allocation is still
    target 1 / held 1.
  - Events are still 15. No allocation or event was written.
- **Post-checks:**
  - `DRY_RUN="true"` in Cloudflare settings and `/health`.
  - The heartbeat returned `dry_run:true` 13 of 13 times.
  - Cloudflare shows 0 errors.

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
