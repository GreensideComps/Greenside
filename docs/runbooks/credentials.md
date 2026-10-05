# Credentials — inventory and handling

**Names and scopes only. Never put a value in this file, in any other file, in a commit, in
memory, in logs or in chat.**

## Handling rules

- Refer to credentials by environment-variable NAME only. Scripts read them from the
  environment and never print them.
- Session environment variables are set by the owner in the cloud environment settings; a new
  session picks up changes.
- If a value is ever exposed (pasted in chat, printed, committed), record it in `open-items.md`
  and rotate it.
- Rotating a credential that the QA Worker also holds means updating the Worker secret at the
  same time.

## Session environment variables

| Name | What it is | Scope / permissions |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | Account-owned Cloudflare API token | Observed in use: deploy and settings for the QA Worker, D1 queries, `wrangler tail`; Workers "Metadata Read-Only" on `greenside-entry-allocator-qa` (Workers Logs, added 2026-09-29). The token cannot read its own permission list. |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account identifier | (identifier, not a secret) |
| `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` | Greenside Entry Allocator app (client-credentials grant) | `read_orders`, `read_products` |
| `QA_STRESS_SHOPIFY_CLIENT_ID` / `QA_STRESS_SHOPIFY_CLIENT_SECRET` | Greenside QA Stress Driver app | `write_draft_orders` only; QA concurrency tests only. Shopify also lists `read_draft_orders` in `accessScopes`: the read implied by a write scope (verified 2026-09-30); the token's own scope is `write_draft_orders`. `gs verify` allows exactly that pair |

Other credential-looking names seen in the session environment, not used by Greenside and of unverified origin: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `CLOUDSDK_AUTH_ACCESS_TOKEN` (`gs verify` warns; see `open-items.md`).

## Cloudflare Worker secrets (per Worker; set with `wrangler secret put`, plus `--env qa` for QA)

| Name | Purpose |
|---|---|
| `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET` | Allocator app credentials; the client secret also verifies webhook HMACs |
| `SHOPIFY_WEBHOOK_SECRET` | Webhook signing secret |

Only the QA Worker's secrets exist today (production is not deployed).
