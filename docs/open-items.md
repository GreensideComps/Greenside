# Open items

Status as of 2026-10-05 (after S4 PASS). Each item needs its own approval before any change.

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
| No integration branch | Theme (default branch `claude/shopify-live-theme-5bbrdw`), allocator (`claude/epic-gauss-db0icm`), QA and docs (`claude/greenside-allocator-qa-verify-f9at5v`) live on different branches. The current briefing and the S4 close-out are on `claude/compassionate-pascal-5vjgnd`. A separate Git architecture decision. |
| QA evidence not committed | B-series and A-series (including A5) results exist only in session records (historical / uncommitted). Committed: the SW1–SW3 plan, the B3 harness and the S4 evidence (both attempts, under `qa/b3-stress-harness/evidence/`). |
| No production deploy runbook | There is a QA live-window runbook but no step-by-step runbook for deploying the production allocator, provisioning its D1 and registering production webhooks. Write and review one before launch. |

## Concurrency testing

| Item | Notes |
|---|---|
| Genuine concurrency proof | **Closed: S4 PASS, 5 Oct 2026** (`qa/concurrency-testing.md`; evidence `/qa/b3-stress-harness/evidence/s4-2026-10-05-qaj/`). One six-entry competition, four concurrent completions; a correctness proof, not a load test. A5 (inconclusive) and the failed 30 Sep attempt are history. |
| Stress Driver app | Verified 2026-09-30 (S1): app "Greenside QA Stress Driver", token scope `write_draft_orders` (`accessScopes` also lists the implied `read_draft_orders`). S4 proved it can complete draft orders and that the resulting orders are PAID. QA use only. |
| Earlier QA fixtures | D1 also holds QAE, QAF, QAG, QAH and PUT from earlier QA tests (spent). Their Shopify products/orders are part of the QA-data decision above; cleanup only with approval. |
| Spent QA fixtures left in the live store | QAI: product 15905304379766 (ACTIVE, unpublished, stock 6), drafts #D28-#D31 OPEN, D1 QAI pool AVAILABLE. QAJ: product 15915106926966 (ACTIVE, unpublished, 0 available / 6 committed), drafts #D32-#D35 COMPLETED, orders #1025-#1028 PAID and unfulfilled, D1 QAJ1001-QAJ1006 ALLOCATED. Both are live-store objects; cleanup is a separate approved task (do not touch before then). |
| Workers Logs real-window tests | Fixed 2026-10-05: `run-all.sh` runs `wltest.py --no-real` (the fixed 28 Sep windows A13/A14/C1-C5 have aged out of retention); live coverage is the opt-in rolling `b3stress-selftest-cmds/wlreal_recent.py`. |
| Webhook subscription deletion under a burst (analysis, 6 Oct 2026) | Under sustained Shopify throttling the allocator returns HTTP 500; Shopify deletes an Admin-API subscription after 8 consecutive failed deliveries (verified doc wording); the allocator's subscriptions are Admin-API created. A launch burst above the allocator's capacity could therefore delete the production webhooks, leaving recovery to 50-per-run sweeps. MUST FIX BEFORE LAUNCH candidate; no allocator change made. `qa/l1-load-test.md` |
| L1 load test | Stage 1 (offline harness + tests) built; stages 2-4 each need approval. Open: the 500-order B cap limits the staircase to judged steps up to 3.0/s. `qa/l1-load-test.md` |
| Unexplained environment credential names | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `CLOUDSDK_AUTH_ACCESS_TOKEN` are present in the session environment (names only; origin unverified; nothing in this repository uses them). Owner to confirm or remove. `gs verify` warns about them. |
| Connector app scopes | The "Shopify Claude Connector App" holds write scopes far beyond any Greenside rule (`write_orders`, `write_products`, `write_inventory`, `write_themes`, `write_customers`, ...). Consider narrowing; needs approval (`operating-layer.md`). |

## Launch and business (not verified in this repository)

| Item | Notes |
|---|---|
| Legal/compliance | Competition model without a skill question, free-entry route, T&Cs: status to confirm. |
| Payment capture | Confirm each payment method captures rather than only authorises (the allocator holds `AUTHORIZED`). |
| Email | The Klaviyo sending domain and welcome flow were unverified as of the 14 Sep briefing (`history/CLAUDE-2026-09-14.md`); re-check. |
