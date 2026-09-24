# Greenside entry allocator

Allocates competition-specific sequential entry numbers to paid Shopify orders.

    valid purchase -> allocation -> entry number -> refund/cancellation handling
      -> auditable, frozen-ready state

That is the whole responsibility. It ends there.

## What this service is NOT

**It does not draw.** No randomness, no beacon, no winner selection, no
commitment hashing, no public proof, no redraw. Those belong to a separate draw
project, which consumes the read-only snapshot in `src/snapshot.ts`.

**It cannot move stock.** There is no inventory write anywhere in the bundle,
and `tests/security.test.ts` fails the build if one appears. The only GraphQL
mutation in the entire service is the order-metafield mirror.

**It is not the retired closer.** `greenside-competition-closer/` is a separate,
retired Worker that *does* write inventory. Nothing here imports from it.
`src/logging.ts` and the HTTP core of `src/shopify.ts` were COPIED, with every
write path stripped. See the provenance note at the top of `src/shopify.ts`.

## Numbering

Ranges are inclusive of both endpoints, and every part is per-competition
configuration. Nothing is hard-coded.

| Product metafield | Example | Meaning |
|---|---|---|
| `custom.entry_prefix` | `PUT` | 2-6 uppercase letters, globally unique |
| `custom.entry_start_number` | `1001` | first number in the range |
| `custom.entries_total` | `100` | capacity -- competition DATA, never inferred |
| (derived) `pad_width` | `4` | digits, sized to the highest number |

    PUT / 1001 / 100  ->  PUT1001 .. PUT1100   (exactly 100: 1100-1001+1)
    DRV / 5001 / 250  ->  DRV5001 .. DRV5250

## How allocation works

The pool is **materialised**: one row per number. "Next number" is the lowest
row whose status is `AVAILABLE` -- a query, not arithmetic. `next = sales + 1`
is wrong the moment anything is refunded, and is never used.

    AVAILABLE --claim--> ALLOCATED --release--> RELEASED --(while OPEN)--> AVAILABLE
                                                         --(once FROZEN)--> terminal

`db.ts :: CLAIM_LOWEST` is one SQL statement that is atomic, all-or-nothing,
lowest-first, and stamps `allocation_id` onto the rows it claims. Read its
comment before changing anything.

Each convergence is ONE `db.batch()`, which D1 commits as a single
transaction: the ledger row (first time only), the claim or release, every
`ALLOCATED` / `RELEASED` / `RETURNED_TO_POOL` event and the ledger counts. A
failure leaves nothing behind, so a retry rebuilds state *and* history. The
claim and release take the target, not a count, and work out the difference
in SQL, so two concurrent runs for one order line cannot over-claim. Freeze
refuses with `AUDIT_GAP` if any number's event history does not match its
state (see `db.ts :: SELECT_AUDIT_GAPS`).

## Refunds and cancellations

One rule covers refund, partial refund, cancellation and order edit:

    target = lineItem.currentQuantity x entries_per_unit

    held > target  -> release the HIGHEST (held - target), LIFO
    held < target  -> claim the LOWEST (target - held)
    held = target  -> nothing

A money-only refund carries no refund line items, so `currentQuantity` does not
move, so nothing is released. No special case needed.

## Payment gate

Allocate only when `displayFinancialStatus == PAID` **and** `cancelledAt == null`
**and** `test == false` **and** the competition is `OPEN`.

`PENDING`, `AUTHORIZED` and `PARTIALLY_PAID` **hold**. `AUTHORIZED` holds
because an authorisation can still be voided or expire, and money that can
vanish must not buy an entry number. PayPal's capture behaviour is deliberately
unestablished; if it turns out to authorise rather than capture, the fix is a
Shopify capture setting, not a weakening of this rule.

## Setup

Requires its own Shopify Dev Dashboard app, installed on the store and in the
same Shopify organisation, holding ONLY:

    read_orders, read_products

**Never `write_inventory`.** Do not reuse the connector app's credentials.

Without `read_all_orders`, only orders from the last 60 days can be read.
`write_orders` is not needed: the order-metafield mirror
(`setOrderEntryNumbersMetafield`) exists but nothing calls it yet, and wiring
it up would need that scope.

`read_customers` is not needed either: the order is read without its customer,
so `customer_ref` is always NULL and an entrant is identified by order.

### Authentication

The Worker holds the app's **client ID and client secret**, never an access
token. A Dev Dashboard app has no permanent `shpat_` token to copy: the Worker
exchanges its credentials for an offline access token with Shopify's
client-credentials grant (`src/auth.ts`). That token always expires after 24
hours and comes with no refresh token, so renewal is another exchange:

- fetched on first use and kept only in the isolate's memory, never in D1, KV
  or a log line;
- reused while valid and replaced 5 minutes before it expires;
- one exchange at a time, however many requests are waiting;
- a 401 drops the token, and the request is retried once with a fresh one;
- a failed exchange fails the webhook with 500, so Shopify redelivers it;
- the granted scopes are checked first: a token with `write_inventory`, or
  without `read_orders` and `read_products`, is refused.

Webhooks are signed with the same client secret, so `SHOPIFY_WEBHOOK_SECRET`
holds that value too.

Production (worker `greenside-entry-allocator`):

    npx wrangler d1 create greenside_entries     # put the id in wrangler.toml
    npx wrangler d1 migrations apply greenside_entries --local
    npx wrangler secret put SHOPIFY_CLIENT_ID
    npx wrangler secret put SHOPIFY_CLIENT_SECRET
    npx wrangler secret put SHOPIFY_WEBHOOK_SECRET

QA (worker `greenside-entry-allocator-qa`, D1 `greenside_entries_qa`). Every
QA command needs `--env qa`; without it Wrangler targets the production
worker name.

    npx wrangler secret put SHOPIFY_WEBHOOK_SECRET --env qa
    npx wrangler secret put SHOPIFY_CLIENT_ID --env qa       # read only when DRY_RUN is "false"
    npx wrangler secret put SHOPIFY_CLIENT_SECRET --env qa   # read only when DRY_RUN is "false"

`DRY_RUN` defaults to true and is disabled only by the exact string `"false"`.
An unset, misspelled or empty value leaves the Worker read-only.

## Webhooks

`orders/create`, `orders/paid`, `orders/cancelled`, `orders/edited`,
`refunds/create`. A webhook is a TRIGGER: the handler takes only the order id,
re-reads the canonical order from the Admin API and decides from live state.
Delivery order therefore does not matter, and neither does which topic fired.

HMAC is verified over the **raw body before any JSON parse**, in constant time,
with the shop domain checked. There is deliberately no timestamp freshness
window -- Shopify retries for 48 hours.

Dispute handling is absent: `read_shopify_payments_disputes` is not available.

## Reconciliation sweep

Webhooks carry the latency; the sweep carries the guarantee. It recovers a
delivery that never arrived, or one acknowledged while the Worker was in dry
run, by converging the order exactly as its webhook would have
(`src/reconcile.ts`). It keeps no state: each run's look-back is fixed by
which cron fired.

| Cron | Mode | Orders listed |
|---|---|---|
| `*/15 * * * *` | trailing | updated in the last 2 hours |
| `RECONCILE_DEEP_CRON` (`0 3 * * *`) | deep | created in the last 59 days |

It compares first: the listing carries each order's status, cancellation,
refunds and per-line `currentQuantity`, which are checked against the pool
using the same eligibility and quantity rules as `processOrder`. Only an order
that has drifted is re-read and converged, as actor `system:reconcile` with no
webhook id. A release it performs is recorded as `CANCELLED` if the order is
cancelled, else `REFUND` if it has a refund, else `ORDER_EDIT`.

- **Dry run** runs the sweep in report mode: it reads Shopify and D1, logs
  `reconcile_would_change`, and writes nothing.
- **FROZEN** competitions take no new numbers: a paid line still owed numbers
  is logged as `reconcile_refused_not_open` for a human to refund. Releases
  still apply and leave the number `RELEASED`.
- **Unreadable orders** (`order_unreadable`) are never released on. Without
  `read_all_orders` Shopify only exposes the last 60 days of orders, so
  `reconcile_aged_allocation` warns when numbers are held on an order 55 or
  more days old: freeze and draw before then.
- **Before a freeze**, wait for a deep run that finished after sales closed
  and logged no drift (`reconcile_summary` with `mismatched: 0`).

Each run ends with one `reconcile_summary` line; `reconcile_truncated` means
the run's page or convergence budget ran out and the next run continues.

## Tests

    npm test          # 339 tests
    npm run typecheck

The D1 stub is backed by real SQLite (`node:sqlite`), not a fake, because the
most important guarantees here are database constraints:
`ux_entry_number_global`, the prefix CHECKs, and the atomicity of the claim.
