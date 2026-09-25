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
