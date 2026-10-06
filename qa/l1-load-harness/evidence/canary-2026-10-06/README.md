# L1 Stage 2 canary — 6 Oct 2026 (QA Worker DRY_RUN=true throughout)

Mutations (exactly three, all in the shared live store):
1. 14:56–14:57 BST `productSet` (connector): product 15916653642102 "QA CANARY L1-C (QA ONLY — NOT FOR SALE)", ACTIVE, 0 publications,
   one variant 58904384012662 at £1.00, untracked, requiresShipping false, no metafields (not a competition).
2. 14:57:28 BST `draftOrderCreate` (QA Stress Driver, `l1/canary.py`): draft #D36 (1614955151734), quantity 1, 100% discount, no
   customer/email/phone, tags qa-load + QAL-CANARY. Request id 4e8b2c75-…-1791295048.
3. 14:57:30 BST `draftOrderComplete` (QA Stress Driver): order #1029 (13599260639606). Request id 902fba68-…-1791295049.

Results: PAID, total £0.00 (discount £1.00), tax £0.00, no tax lines, no transactions, no gateway, not test, not cancelled,
requiresShipping false, one OPEN fulfillment order. Webhooks (Workers Logs, complete window): orders/paid 13:57:30.745Z and
orders/create 13:57:32.461Z (Shopify triggered-at), each delivered once, HTTP 200, `dry_run_webhook`, version d5710fab.
Allocator skip (real reconciliation code, report mode): trailing 14:00 UTC orders_seen 1 (the canary), in_scope 0, mismatched 0;
deep 13:50 UTC before = seen 28 / in_scope 19, deep 14:20 UTC after = seen 29 / in_scope 19. QA D1 unchanged (7 / 19 / 47 / 137).
Klaviyo: Placed Order and Ordered Product 0 before and 0 at +25 min. No third-party app event, metafield or tag on the order.

Files: canary.json (tool output), canary-product.json, d1-competitions.json, shopify-and-state.json, wl-window.json (webhooks),
wl-sweep-1400.json, wl-sweeps-1415-1420.json, wl-deep-1350-precanary.json, t-start-ms / t-end-ms. Verify: sha256sum -c MANIFEST.sha256
