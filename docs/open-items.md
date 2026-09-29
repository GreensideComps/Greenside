# Open items

Status as of 2026-09-29. Each item needs its own approval before any change.

## Safety and configuration

| Item | Notes |
|---|---|
| Credential rotation | The Cloudflare API token and the allocator Shopify client secret were exposed in chat on 2026-09-29. Rotating the allocator secret also requires updating the QA Worker secret. |
| `.claude/settings.json` (branch `claude/epic-gauss-db0icm`) | Pre-approves Shopify write tools (GraphQL mutation, product and collection create/update) against the live store. Consider requiring a prompt. |
| Retired `greenside-competition-closer/` | Still in the repository with a deployable `wrangler.toml` and inventory writes. Not deployed. Decide whether to mark it or remove its deploy config. |
| QA data in the live store | QA competition products and QA orders exist in the live store (shared store). Decide how they are handled before launch. |

## Repository

| Item | Notes |
|---|---|
| No integration branch | Theme (default branch `claude/shopify-live-theme-5bbrdw`), allocator (`claude/epic-gauss-db0icm`), QA and docs (`claude/greenside-allocator-qa-verify-f9at5v`) live on different branches. This briefing currently exists only on the QA branch. A separate Git architecture decision. |
| QA evidence not committed | B-series and A-series results exist only in session records (historical / uncommitted). Only the SW1–SW3 plan and the B3 harness are committed. |

## Concurrency testing

| Item | Notes |
|---|---|
| Genuine concurrency proof | A5 INCONCLUSIVE. Next: QA Stress Driver S1 → S4 (`qa/concurrency-testing.md`). |
| Stress Driver app | Configured with `write_draft_orders`; installation and credentials to be verified in S1. |

## Launch and business (not verified in this repository)

| Item | Notes |
|---|---|
| Legal/compliance | Competition model without a skill question, free-entry route, T&Cs: status to confirm. |
| Payment capture | Confirm each payment method captures rather than only authorises (the allocator holds `AUTHORIZED`). |
| Email | The Klaviyo sending domain and welcome flow were unverified as of the 14 Sep briefing (`history/CLAUDE-2026-09-14.md`); re-check. |
