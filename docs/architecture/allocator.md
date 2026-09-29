# Entry allocator — architecture

**Source:** Repo — `greenside-entry-allocator/` on branch `claude/epic-gauss-db0icm` at commit
`e917bb5` (README, `wrangler.toml`, `src/`). This branch does not contain that code; read it
there. The allocator README is the detailed reference; this page is a summary.

## Responsibility

Allocate competition-specific sequential entry numbers to paid Shopify orders, and keep them
correct through refunds and cancellations until the competition is frozen:

    valid purchase -> allocation -> entry numbers -> refund/cancellation handling -> frozen state

It does **not** draw winners (no randomness or winner selection; a separate draw project reads
the frozen snapshot). It has **no inventory write**; a security test fails the build if one
appears. Its only Shopify mutation, an order-metafield mirror, is not called and would need
`write_orders`, which the app does not hold.

## Flow

- **Webhooks**: `orders/create`, `orders/paid`, `orders/cancelled`, `orders/edited`,
  `refunds/create`. Each is HMAC-verified and converges the order's allocation.
- **Reconciliation sweeps** (Cron): a trailing sweep every 15 minutes (orders updated in the last
  2 hours) and a deep sweep (orders created in the last 59 days), which recover anything a
  webhook missed. Production runs the deep sweep daily at 03:00 UTC; QA at :20 and :50.
- **Convergence**: each order converges atomically in one D1 batch. Numbers are claimed
  lowest-first; releases are recorded as events; an event ledger gives a full audit trail.
- **Freeze**: closes a competition and produces the read-only snapshot for the draw.

## Rules

- **Payment gate**: allocate only when `displayFinancialStatus == PAID`, `cancelledAt == null`,
  `test == false` and the competition is `OPEN`. `PENDING`, `AUTHORIZED` and `PARTIALLY_PAID`
  hold.
- **Capacity** comes from product metafields (`custom.entry_prefix`,
  `custom.entry_start_number`, `custom.entries_total`) and is never inferred.
- **Skill mode**: `custom.skill_mode = none` means no question (entries recorded
  `NOT_REQUIRED`). Unset or `required` means the legacy question mode. Any other value is a
  configuration error that blocks freeze. The launch model requires `none`, set before the
  competition opens and never changed afterwards (freeze refuses on `SKILL_MODE_MISMATCH`).
  Needs migration `0005`.
- **DRY_RUN**: a plain variable; only the exact string `"false"` permits writes.

## Environments

| | Production | QA |
|---|---|---|
| Worker | `greenside-entry-allocator` | `greenside-entry-allocator-qa` |
| D1 | `greenside_entries` (id not yet set in `wrangler.toml`) | `greenside_entries_qa` |
| Wrangler | default environment | `--env qa` (required on every QA command) |
| Shopify store | `7r5csb-1j.myshopify.com` | the SAME store |

Deployment state: see `CLAUDE.md` → Current state.

## Authentication

Client-credentials grant with the Dev Dashboard app's client ID and secret (Worker secrets).
The access token is held only in memory and renewed before expiry. See
`docs/runbooks/credentials.md`.
