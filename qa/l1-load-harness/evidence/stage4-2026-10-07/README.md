# L1 Stage 4 live run — 7 Oct 2026 (owner-approved; one run; QA Worker/QA D1 only)

Result: **LOAD STOP after 8 of 750 orders (baseline phase A, 0.5/s)** on the first failed webhook delivery, as designed.
Then drained, planned restore, post-check PASSED. Staircase never entered. **L1 verdict: FAIL** (a genuine allocator defect stopped
the run at baseline); the throughput question is NOT answered by this run.

Timeline (UTC; BST = UTC+1)
- 11:42:48 window.sh live (state dir = fresh B3 install); prelive: install, Worker d5710fab DRY_RUN true, monitors, prep
  (expected-subs, pre-max-order #1029, d1-ref, 750/750 drafts OPEN), slot, strict sweeps PASS, tail continuity INTACT
  (155/155 probes), 0 webhooks, D1 = reference, drafts re-read 750/750 OPEN, state check PASS.
- 12:01:24 Workers Logs poller from wl_from_ms 1791374484444; gate.sh deploy --env qa DRY_RUN:false -> 55b7889e;
  strict gate PASSED 12:02:25 (5 x dry_run=false over 21 s, tail-confirmed, no other-version request).
- 12:02:31 guardl-config, Worker poller, sampler, guardl.sh; guard clear; pre-GO (0 webhooks, D1 unchanged); armed; load.py live.
- 12:02:36-12:02:50 phase A at 0.5/s: 8 draftOrderComplete, all SUCCESS (HTTP 200, request ids, Stress Driver bucket 1990/2000).
- 12:02:49.104 orders/create for #1034 -> HTTP 500 (wall 564 ms):
  `webhook_failed D1_ERROR: UNIQUE constraint failed: allocation.order_id, allocation.line_item_id` (the concurrent
  orders/paid delivery for the same order had inserted the allocation first). Failwatch LOAD STOP -> governor stopped sending
  (12:02:50); load.py exit 3; 742 drafts never sent (still OPEN).
- 12:02:50.47 Shopify re-delivered the same webhook (1e594a0b-…): HTTP 200, webhook_processed.
- 12:02:58 guard: D1 allocated 13 = units of the 8 completed drafts -> DRAINED -> planned restore (window.sh restore ->
  B3 restore.sh): deploy --env qa -> 004e753f, restore gate PASSED 12:03:45, verified 12:03:47.
- 12:16:16 postcheck PASSED (first */15 dry-run sweep on 004e753f all zeros, in_scope 8; D1 identical to the last live
  snapshot; integrity 0). Final failwatch + final state check PASS. Monitors stopped.

Results
- Orders: 8 completed (#1030-#1037, drafts #D37-#D44), PAID, £0.00, no customer/email/phone; 742 drafts OPEN.
- D1: 8 allocations (all source webhook, status ALLOCATED), 13 entries QAL1001-QAL1013 each issued once (allocation_seq 1),
  13 ALLOCATED events (system:webhook); unallocated entries 0; no duplicates, gaps or over-allocation; integrity all 0.
  QAL pool: 13 ALLOCATED, 1987 AVAILABLE.
- Webhooks (tails = Workers Logs): 17 invocations on 55b7889e: orders/paid 8 x 200, orders/create 8 x 200 + 1 x 500.
  Workers Logs: 28 windows, all complete, no gap, 227 records; the failure confirmed (0 unconfirmed).
- Concurrency: peak Worker/webhook concurrency 2; wall times 303-1165 ms. Allocator bucket min 1994/2000; 0 THROTTLED.
- Inventory: 1650 -> 1637 (13 units).
- loadrecon: 33/34 PASS; FAIL d1.zero_value_lines: allocation.line_total_minor = qty x 100 (pre-discount) while
  unit_price_minor = 0. Allocator source: line total from discountedTotalSet(withCodeDiscounts: true), which excludes the
  draft's order-level discount; unit price from discountedUnitPriceAfterAllDiscountsSet. Recording inconsistency, not an
  allocation error. The check was not changed.
- Metrics (loadmetrics, OBSERVED): arrivals 1.0/s peak over 10 s (2-s pacing), completion latency p50 2.63 s / p95 2.85 s,
  peak backlog 2, drain 2.2 s; stability point not determined (staircase did not run).

Files: tails pre/pre2/conf (.jsonl.gz), heartbeat.log, gate/restore/postcheck logs, guardl.* , failwatch*.json,
worker-poll.jsonl, wl-poll.jsonl, sampler.jsonl, load-evidence.jsonl, load-summary.json, orders-export.json,
drafts-final.json, d1-ref.json / d1-final.json / d1-post*.json, recon*.json, metrics*.json, window-state-*.json,
window.log, window-run.out. Verify: sha256sum -c MANIFEST.sha256
