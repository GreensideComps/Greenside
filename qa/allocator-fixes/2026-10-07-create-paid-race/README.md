# Allocator fix (OFFLINE, not deployed): concurrent orders/create + orders/paid ledger race

Found live by L1 Stage 4 (7 Oct 2026, `/qa/l1-load-harness/evidence/stage4-2026-10-07/`): orders/create for #1034 returned HTTP 500
`D1_ERROR: UNIQUE constraint failed: allocation.order_id, allocation.line_item_id`. Shopify's retry then returned 200.

Base: `greenside-entry-allocator/` at e917bb5 (branch `claude/epic-gauss-db0icm`, the deployed QA code). Fix commit (local, not pushed
to the allocator branch): 610e1899f352c09848c3bbc79630a0a9289d5658, exported here as `0001-resolve-concurrent-first-delivery-ledger-race.patch`
(sha256 2fd428ed6639b09936bb1ecccd2ec72899a57c82de558c3c3301637af5b36f6c).
Apply: `git checkout e917bb5 && git am 0001-*.patch`. Landing it on the allocator branch, and any deploy, need separate approval.

## Root cause
`src/process.ts` `processLine()`: it reads the ledger row (`SELECT_ALLOCATION` by the deterministic allocation_id); when there is none
it builds the ledger `INSERT_ALLOCATION` (a plain INSERT) as the prelude of the single convergence batch (`src/converge.ts`
`converge()`: INSERT ledger, `CLAIM_TO_TARGET`, events, counts, one D1 transaction). Two deliveries for the same order both read "no
row" before either commits, so both send the INSERT. D1 runs batches one at a time: the first commits; the second violates the
allocation table's identity and is rolled back whole, the exception reaches `src/index.ts` and becomes `webhook_failed` + 500. The
claim itself was already race-safe (CLAIM_TO_TARGET re-measures holdings inside the transaction); only the ledger INSERT was not.
Two existing tests encoded the old contract ("the losers fail cleanly and a retry is a no-op").

## Fix
`processLine()` wraps the convergence. It re-runs the line ONCE, as an ordinary later delivery (row present: no INSERT, entries per
unit from the ledger, converge from the pool, normally NONE), only when ALL hold:
1. this run sent the ledger INSERT (`prelude.length > 0`), and is not already the re-run;
2. the error is a UNIQUE violation on the allocation table's identity only: `allocation.allocation_id` or
   `allocation.order_id, allocation.line_item_id`, ending there (`isLedgerIdentityConflict`);
3. a fresh read finds the row under THIS allocation_id with the same shop, competition, order and line.
Everything else is rethrown (still 500): other tables' UNIQUE (entry numbers, events), a row for the line under another
allocation_id, a mismatching row, any non-UNIQUE error, any failure of the re-run. The resolved race logs `allocation_race_resolved`.

Why safe under concurrency: the winner's batch is atomic, so when the loser sees the committed row the numbers and events are already
complete; the re-run's batch contains no INSERT and its CLAIM_TO_TARGET / event inserts are idempotent in SQL (held == target -> no
claim, events only for issues without one). One re-run, no INSERT on it, so no loop; a repeat of the race on the re-run is rethrown.

## Tests (vitest, real SQLite-backed D1, Admin API mocked; no network)
- New `tests/webhook-race.test.ts` (24): Stage 4 regression through the Worker `fetch()` with a barrier that guarantees both
  deliveries read the empty ledger before either commits; create->paid and paid->create sequential; concurrent create+paid; natural
  interleaving; same webhook twice concurrently; 5 repeated deliveries; 4 different webhook ids racing; a later delivery sends no
  ledger INSERT; the second delivery's 200 `{status: ok, outcomes: [NONE]}`; genuine failures stay failures (8a-8k: different
  allocation_id, other-table UNIQUE, no committed row, re-run failure, bounded re-run, non-UNIQUE errors, message matching, field
  mismatch x4). Every race scenario asserts exactly one allocation, numbers PUT1001.. issued once, events == units, no duplicates, no
  ledger drift.
- Changed: `tests/atomicity.test.ts` (5 concurrent first deliveries: all now succeed) and `tests/reconcile.test.ts` (sweep vs webhook on
  an empty ledger: both succeed, sweep errors 0, one `allocation_race_resolved`).
- Results: 389/389 (365 existing + 24 new), `tsc --noEmit` clean, also after `git am` onto a fresh e917bb5.
- Against the ORIGINAL process.ts the new tests fail 9 (the regression: `[200, 500]` instead of `[200, 200]`).
- `mutation-check.py` (run: `python3 mutation-check.py <allocator dir>`): 14/14 mutants of the fix killed.
