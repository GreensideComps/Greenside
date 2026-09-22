# Greenside competition closer

A scheduled Cloudflare Worker with one job: when an opted-in competition's
`custom.closing_at` has passed, set its **available inventory to exactly 0** so
Shopify itself refuses further checkout, then add an audit tag so the
competition is never processed again.

It only ever closes. It never restores inventory, removes a tag, changes a
product's status, or edits `closing_at`. **Reopening is a manual operation.**

It does not touch the theme, storefront, orders, payments, customers or
checkout.

---

## 1. Architecture

```
Cloudflare Cron (*/5 * * * *, UTC)
      ↓
scheduled() handler
      ↓
Shopify Admin GraphQL API (2026-07)
      ↓
Discover: status:active AND tag:competition-live AND -tag:gs-closed-zeroed
      ↓
Defensive eligibility checks (pure, in-Worker)
      ↓
closing_at <= now (UTC)?
      ↓
inventorySetQuantities → available = 0   (compare-and-set + @idempotent)
      ↓
Fresh read → verify available === 0
      ↓
tagsAdd → gs-closed-zeroed
```

**Why the date comparison happens in the Worker.** Shopify's product search
**silently ignores** an unsupported metafield predicate rather than erroring.
Verified against the live store: `metafields.custom.closing_at:<'2020-01-01'`
and `metafields.custom.closing_at:>'2099-01-01'` are mutually exclusive, yet
both returned the same two products. A filter that appears to work while
returning everything is the worst possible failure mode here, so the search is
restricted to predicates that were verified to work (`status:`, `tag:`,
`-tag:`) and every returned product is date-checked in code.

**Why inventory rather than unpublishing.** Competition variants are
`tracked: true` with `inventoryPolicy: DENY`, so available = 0 is a
server-side stop at checkout. Unpublishing would 404 the page and break links
already sent to entrants.

**Time.** Cron Triggers execute on UTC. Every comparison is done in epoch
milliseconds via `Date.parse`, which resolves offsets to absolute instants.
There is no local-time or BST arithmetic anywhere in the codebase.

### Safety model

| Gate | Behaviour |
|---|---|
| `DRY_RUN` | Defaults to on. Only the exact string `"false"` permits a write. |
| `status === ACTIVE` | Drafts are never mutated (this is what protects Qi10). |
| `competition-live` | Opt-in. Absent → skip. No product is closed by default. |
| `gs-closed-zeroed` | Present → skip. Idempotency and audit marker. |
| `closing_at` | Must exist, be a real calendar timestamp, and be `<= now`. |
| Exactly one variant | Anything else → `UNEXPECTED_VARIANT_STRUCTURE`, no mutation. |
| `ALLOWED_LOCATION_ID` | The only location whose quantity may be written. |
| Verify-before-tag | The tag is added only after a fresh read returns 0. |

---

## 2. Required Shopify scopes

```
read_products
write_products
read_inventory
write_inventory
```

Confirmed by validating each operation against the Shopify schema:

| Operation | Scopes reported |
|---|---|
| `products` discovery query | `read_products`, `read_inventory` |
| `inventoryItem` verification read | `read_inventory`, `read_products` |
| `inventorySetQuantities` | `write_inventory`, `read_inventory` |
| `tagsAdd` | polymorphic; tagging a **Product** requires `write_products` |

**`write_products` is unavoidable.** `tagsAdd` mutates the product. The task
brief listed `write_products` as both required and forbidden — it is required,
and there is no way to add the audit tag without it. `read_inventory` was not
in the brief's list but is reported by the schema validator for every
inventory operation.

No order, customer, theme or payment scope is requested.

---

## 3. Shopify app setup

1. Shopify Admin → **Settings → Apps and sales channels → Develop apps**.
2. **Create an app**, name it `Greenside Competition Closer`.
3. **Configuration → Admin API integration → Configure**, select exactly the
   four scopes above, **Save**.
4. **API credentials → Install app**.
5. Reveal the **Admin API access token** (`shpat_…`) once and put it straight
   into Cloudflare (section 5). It is shown only once.

This is a custom app installed on your own store, so the token is an **offline
token**: it does not expire and needs no refresh, which is what a background
scheduled Worker requires.

---

## 4. Cloudflare setup

```bash
npm install
npx wrangler login
```

`wrangler.toml` already pins the schedule and the non-secret configuration:

```toml
[triggers]
crons = ["*/5 * * * *"]

[vars]
DRY_RUN = "true"
SHOPIFY_API_VERSION = "2026-07"
ALLOWED_LOCATION_ID = "gid://shopify/Location/119443915126"
REQUIRED_LIVE_TAG = "competition-live"
CLOSED_TAG = "gs-closed-zeroed"
```

---

## 5. Secret configuration

Secrets are **never** in source, in `wrangler.toml`, in logs or in this file.

```bash
npx wrangler secret put SHOPIFY_STORE
# paste: greensidecompetitions.myshopify.com

npx wrangler secret put SHOPIFY_ACCESS_TOKEN
# paste the shpat_… token; it is not echoed
```

Verify they exist without revealing values:

```bash
npx wrangler secret list
```

To rotate: delete the app's token in Shopify, create a new one, re-run
`wrangler secret put SHOPIFY_ACCESS_TOKEN`, redeploy.

The logger scrubs any string matching the access token from every emitted
line, and redacts fields whose names look like credentials. That is a second
line of defence, not the primary one — no code path logs the token.

---

## 6. Local development

```bash
cp .dev.vars.example .dev.vars   # .dev.vars is git-ignored
# edit .dev.vars with a real token ONLY if you intend to hit the real API
npm run dev
```

Trigger the scheduled handler locally:

```bash
curl "http://localhost:8787/cdn-cgi/handler/scheduled"
```

Health check (cannot close anything — it never builds a Shopify client):

```bash
curl http://localhost:8787/health
```

---

## 7. Unit testing

```bash
npm test        # vitest, 86 tests
npm run typecheck
```

Tests cover date logic (including offsets and impossible calendar dates), all
tag/status/inventory gates, compare-and-set conflict and retry, verification
failure, tag failure, partial-failure recovery, dry-run enforcement and secret
scrubbing. No test touches the network.

---

## 8. Dry-run procedure

`DRY_RUN=true` is the deployed default. In this mode all reads happen, no
mutation is possible, and each eligible product emits:

```json
{"event":"WOULD_CLOSE","product_id":"...","product_title":"QA-3",
 "closing_at":"...","current_inventory":500,"target_inventory":0,
 "would_add_tag":"gs-closed-zeroed"}
```

Watch a live run:

```bash
npx wrangler tail --format pretty
```

---

## 9. QA-3 integration test procedure

Run against **QA-3 only**. QA-1, QA-2 and Qi10 must not carry
`competition-live` at any point during these tests.

| # | Setup | Expected |
|---|---|---|
| 1 | QA-3: `competition-live`, `closing_at` in the past, inventory > 0. `DRY_RUN=true` | `WOULD_CLOSE` logged. **Zero Shopify writes.** Inventory unchanged |
| 2 | QA-3: `closing_at` in the future | `SKIP_NOT_YET_CLOSING`. No write |
| 3 | `DRY_RUN=false`, `closing_at` past, inventory a known positive number | Inventory → 0; verify in Admin |
| 4 | after test 3 | `gs-closed-zeroed` present on QA-3 |
| 5 | run again | Discovery excludes QA-3 (`-tag:gs-closed-zeroed`). No mutation |
| 6 | manually remove the tag, leave inventory at 0 | Next run skips the mutation (`SKIPPED_ALREADY_ZERO`) and re-adds the tag |

Between tests, set `DRY_RUN` back to `"true"`:

```bash
npx wrangler deploy --var DRY_RUN:true
```

---

## 10. Production activation procedure

1. All unit tests green, all six QA-3 tests observed.
2. Confirm no unintended product carries `competition-live`:
   `status:active AND tag:competition-live`.
3. Deploy with the cron enabled and dry run still on; watch one full cycle.
4. Flip to live by setting `DRY_RUN = "false"` in `wrangler.toml` and
   `npx wrangler deploy`.
5. Tag the real competition `competition-live` **last**. Until a product
   carries that tag, the Worker selects nothing — this is the safest
   possible resting state.

---

## 11. Rollback and recovery

**Stop the automation** (no data change):

```bash
npx wrangler deploy --var DRY_RUN:true   # immediate, keeps the cron running
npx wrangler delete                       # removes the Worker entirely
```

**Reopen a competition that was closed** (manual, in order):

1. Remove `gs-closed-zeroed`.
2. Set `closing_at` to the new closing time.
3. Restore inventory to `entries_total − entries actually sold`.

The number for step 3 is recoverable even though zeroing destroyed it: entries
sold is the sum of line quantities for that variant across orders where
`financial_status == paid AND cancelled_at == null`.

**Remove `competition-live`** from a product to exclude it from all future
runs without deleting anything.

---

## 12. Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| `missing_configuration` | A secret is unset | `npx wrangler secret list` |
| `authentication or scope failure` (401/403) | Bad or under-scoped token | Re-check the four scopes; reinstall the app |
| `discovery_failed` `THROTTLED` | Rate limit | Self-resolves; client retries with backoff |
| `UNEXPECTED_VARIANT_STRUCTURE` | Product has ≠1 variant | Intentional. Close by hand |
| `SKIP_MISSING_INVENTORY_LEVEL` | Not stocked at the allowed location | Check the location, or the product is not a competition |
| `SKIP_INVALID_CLOSING_AT` | Metafield is not a real timestamp | Fix the metafield |
| `VERIFICATION_FAILED` | Read-back was not 0 | **No tag added.** Investigate before re-running |
| `TAG_FAILED` | Inventory is 0, tag missing | Next run completes the tag automatically |
| `IDEMPOTENCY_KEY_PARAMETER_MISMATCH` | Should not occur | The key includes the observed quantity; report it |

---

## 13. Security considerations

- Secrets live only in Cloudflare encrypted Worker Secrets. `.gitignore`
  excludes `.dev.vars` and `.env`.
- No credential appears in source, config, logs, error messages or this file.
- The token is never sent anywhere but `https://<store>/admin/api/...`.
- 401/403 is never retried — retrying a bad token wastes the run and looks
  like an attack.
- The HTTP endpoint serves `/health` only. It never constructs a Shopify
  client, never reads the access token, and ignores all query parameters, so
  it cannot be used to trigger a close.
- Least privilege: four scopes, no order/customer/theme/payment access.
- The Worker cannot delete, restore or reopen anything. Its only write verbs
  are "set this one location's available quantity to 0" and "add one tag".
