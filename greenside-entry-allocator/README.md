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

Requires its own Shopify custom app holding ONLY:

    read_orders, read_all_orders, read_products, write_orders

**Never `write_inventory`.** Do not reuse the connector app's token.

    npx wrangler d1 create greenside_entries     # put the id in wrangler.toml
    npx wrangler d1 migrations apply greenside_entries --local
    npx wrangler secret put SHOPIFY_ACCESS_TOKEN
    npx wrangler secret put SHOPIFY_WEBHOOK_SECRET

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

## Tests

    npm test          # 232 tests
    npm run typecheck

The D1 stub is backed by real SQLite (`node:sqlite`), not a fake, because the
most important guarantees here are database constraints:
`ux_entry_number_global`, the prefix CHECKs, and the atomicity of the claim.
