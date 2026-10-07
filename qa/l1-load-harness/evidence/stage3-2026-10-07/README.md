# L1 Stage 3 fixture — 7 Oct 2026 (QA Worker DRY_RUN=true throughout; no order created)

Approved by the owner on 7 Oct 2026 (Stage 3 only; Stage 4 not approved). Committed mechanisms only.

1. 12:11 BST `productSet` (Shopify connector): product 15918227030390 "QA — Allocator LOAD Test L1 (QA ONLY — NOT FOR SALE)",
   handle qa-allocator-load-test-l1, ACTIVE, 0 publications, tags qa-only/qa-load/QAL, custom.entry_prefix QAL,
   entry_start_number 1001, entries_total 2000, skill_mode none; one variant 58912245252470 at £1.00, tracked, DENY,
   1650 available at Shop location, requiresShipping false. `stage.check_product`: no problems (`PRODUCT.json`).
2. 12:11:57 BST `register.sh 15918227030390 ... REGISTER-QAL-2000-ENTRIES` (QA D1 only, --env qa): competition OPEN,
   capacity 2000, QAL1001..QAL3000 all AVAILABLE, allocation_seq 0 (`register.log`).
3. 12:12-12:24 BST `stage.py execute --confirm STAGE-QAL-750-DRAFTS` (QA Stress Driver): 750 drafts #D37..#D786, all CREATED
   (HTTP 200, total 0.0, no userErrors), never retried. Bound plan sha256 6fa7442b9089ea384fbf3eb478b50c49f37abaabfa4787f5552a068a496cc1f5.

Verification (read-only):
- `drafts-verify.json`: all 750 read back by exact id (Stress Driver): OPEN, exact name and tags (qa-load, QAL, QAL-NNNN-qQ),
  GBP subtotal/total/tax 0.0, 100% discount, one line item with the planned quantity, requiresShipping false, no email, phone,
  shipping/billing address, shipping line or inventory reservation. Quantity mix 1x300, 2x225, 3x150, 5x60, 10x15 = 1650 units.
- `connector-coverage.json`: every plan draft has no customer, no order and the L1 variant; newest order still #1029.
- `d1-after-stage3.json` (snap.sql): competitions 8 (QAL added), allocations 19, events 47 (both unchanged), entries 2137
  (137 + 2000), webhook deliveries 0, integrity all 0; L1 registration exact; guard allow-list of the snapshot against itself clean.
- Worker: QA only, d5710fab at 100%, DRY_RUN "true", last deployed 5 Oct; production Worker 404; only D1 is greenside_entries_qa;
  5 webhook subscriptions unchanged (gs verify ALL PASS). No gate, guard, poller or load process; no L1 window state.

Files: PRODUCT.json, product-snapshot-after-create.json, register.log, stage-payloads.json, stage-evidence.jsonl,
plan-bound.json, plan-bound.sha256, drafts-verify.json, connector-coverage.json, d1-after-stage3.json.
Verify: sha256sum -c MANIFEST.sha256
