# Production readiness — entry allocator

A checklist, not a record of approval. Every item needs verification and explicit approval at
the time it is done. Status as of 2026-09-29.

| # | Item | Status |
|---|---|---|
| 1 | Production D1 `greenside_entries` created, migrations 0001–0005 applied, id set in `wrangler.toml` | Not done (Repo: id is a placeholder) |
| 2 | Production Worker secrets set (`SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET`, `SHOPIFY_WEBHOOK_SECRET`) | Not done |
| 3 | Production Worker deployed with `DRY_RUN=true` first, verified, then switched deliberately | Not deployed (verified live 2026-09-29) |
| 4 | Shopify webhooks registered to the production Worker for the five order topics | Not done |
| 5 | Allocator app scopes exactly `read_orders`, `read_products` | Verified live 2026-09-29 |
| 6 | Every launch competition product has `custom.skill_mode = none` and its capacity metafields set BEFORE opening | Per product, at setup |
| 7 | `greenside-competition-closer` is never deployed | Not deployed (verified live 2026-09-29) |
| 8 | Genuine concurrency proven | Not yet: A5 INCONCLUSIVE (`qa/concurrency-testing.md`) |
| 9 | QA products and QA orders in the live store handled (hidden, archived or otherwise dealt with) before launch | Open (`open-items.md`) |
| 10 | Exposed credentials rotated | Open (`open-items.md`) |
| 11 | Payment capture behaviour confirmed for each payment method (the allocator holds `AUTHORIZED`) | Open |
| 12 | Legal/compliance sign-off of the competition model (no skill question, free-entry route, T&Cs) | Unverified in the repository (`open-items.md`) |
| 13 | Rollback plan: redeploy with `DRY_RUN=true`, verified by the restore gate | Procedure exists for QA (`runbooks/qa-live-window.md`) |

Lifecycle QA (allocation, refunds, cancellation, sweep recovery, skill-mode none) was carried
out on the QA Worker. Only the SW1–SW3 plan is committed
(`allocator-qa/SW1-SW3-test-plan.md`); the other results are **historical / uncommitted**.
