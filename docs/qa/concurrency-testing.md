# Concurrency testing

**Goal:** prove that when several orders for the same competition arrive at the same time, the
allocator still assigns every entry number exactly once, with no gaps or duplicates. A run
counts as a genuine concurrency proof only if `overlap.py` observes at least one cross-order
overlap: two webhook invocations for different orders in flight at the Worker at the same time.
With no such overlap, the run is INCONCLUSIVE, however correct its allocations are.

The harness is in the repository: `/qa/b3-stress-harness/` (fire script: `b3stress/fire.py`, `b3stress/FIRE.md`).

## Current result: S4 PASS — 5 Oct 2026 (authoritative)

Status: **Verified live (2026-10-05)**. Evidence preserved, read-only, with checksums:
`/qa/b3-stress-harness/evidence/s4-2026-10-05-qaj/` (`README.md` there has the timeline; verify with `sha256sum -c MANIFEST.sha256`).

**Scope: this proves one six-entry competition with four concurrent Shopify completions. It is a concurrency correctness proof, not a
load/performance test.** QA Worker and QA D1 only; fixture QAJ (product 15915106926966, prefix QAJ, capacity 6).

Proven chain (times BST):

| Layer | Evidence | Result |
|---|---|---|
| Shopify / client | `fire.py live`, one run, released 10:44:50.000 from one barrier; send spread 1.2 ms | 4 `draftOrderComplete` deliveries confirmed, 4 distinct Shopify request ids, all HTTP 200 / COMPLETED; client intervals overlapped (peak 4) |
| Orders | Shopify `draftOrder.order` links | #D32 → #1028 (1), #D33 → #1026 (1), #D34 → #1025 (2), #D35 → #1027 (2); all four PAID (manual SALE), not test, not cancelled; allocator verdict ELIGIBLE |
| Webhooks | redundant tails + Workers Logs | 4 `orders/paid` (plus 4 `orders/create`), all HTTP 200, HMAC verified, on the gated version |
| Worker / allocator | tails (`overlap.py`: CONCURRENT, 8 cross-order overlaps, peak 4) and an independent complete Workers Logs query | the 4 allocating `orders/paid` executions were in flight together for about 380 ms (10:44:53.838-54.218); peak Worker concurrency 4 |
| Allocation correctness | D1 snapshots before / after | QAJ1001-QAJ1006 each allocated exactly once (#1025: 1001-1002, #1027: 1003-1004, #1026: 1005, #1028: 1006); no duplicates, gaps, skips or over-allocation; integrity 0; no other competition, entry or allocation changed |
| Safety | guard, restore gate, post-check, `gs verify` | production Worker absent throughout; QA Worker restored to `DRY_RUN=true` (version `d5710fab`); restore gate and post-check PASSED; evidence intact (204/204 heartbeat probes covered) |

Each allocation was written by a different one of the four overlapping executions (the D1 events carry their webhook ids); D1
committed the claims one after another inside that overlap, which is the expected single-writer behaviour.

What S4 did **not** prove:
- throughput, latency or behaviour under load (one burst of four orders);
- anything about the production Worker or production D1 (neither exists);
- real checkout or payment-method behaviour (orders came from draft completion, manual gateway; capture vs authorisation is still an
  open launch item);
- concurrency between refunds, cancellations or edits, between competitions, or between a webhook and a sweep (no hook/sweep overlap
  occurred in this run);
- anything beyond this fixture's quantities (1, 1, 2, 2 into a pool of exactly 6).

## History

### A5 — 2026-09-29 — INCONCLUSIVE (superseded by S4)

Status: **historical / uncommitted** (evidence is in session records, not in the repository).

- Four draft orders (quantities 1, 1, 2, 2) on an isolated six-entry QA product were completed
  through the Shopify connector in one parallel tool step, with the QA Worker live behind the
  strict gate.
- Correctness: all six numbers allocated exactly once, in contiguous blocks, by the webhook
  path. Evidence stayed intact; the restore and post-check passed; D1 integrity was 0.
- Concurrency: **none**. The connector ran the four calls one after another (orders about 2
  seconds apart), and each webhook finished before the next arrived. `overlap.py` reported
  SEQUENTIAL, peak in flight 1, cross-order overlaps 0.
- Therefore A5 is **not** a genuine concurrency proof and must not be described as one.
- That QA product's pool is now fully allocated and its drafts are used; it is not reusable.

### S1-S3 — 2026-09-30 — passed (preparation)

- S1: four independent HTTPS read-only requests from one container were genuinely concurrent client-side (peak in flight 4). This
  proves nothing about webhook delivery or Worker execution. Stress Driver token scope `write_draft_orders`; `read_draft_orders`
  appears in `accessScopes` as the read implied by the write scope (Shopify: a write scope also grants read).
- S2: fresh QAI fixture (product 15905304379766, capacity 6, drafts #D28-#D31 with quantities 1, 1, 2, 2, product unpublished
  from the Online Store). S3: dry-run pre-live gates passed (121/121 offline checks; fire script added later, see below).

### S4 first attempt — 2026-09-30 — FAIL: execution transport failure

Status: **Verified live (2026-09-30)** for the outcome; the cause is confirmed by a read-only experiment. Evidence preserved unchanged,
with checksums: `/qa/b3-stress-harness/evidence/s4-2026-09-30-failed/` (do not edit; `README.md` there has the timeline).

- The QA Worker was armed correctly (version `ad67fcf2`, `DRY_RUN=false`, strict gate passed, guard running) and `fire.py live` ran
  once at 11:44:50 BST. All four sends failed with `SSLEOFError` about 1 ms after send.
- **No mutation was delivered to Shopify. No allocator execution occurred. That attempt proved nothing about concurrency.** Correct counts: 4 attempts,
  0 confirmed deliveries, 0 Shopify request ids, 0 HTTP responses. Drafts #D28-#D31 stayed OPEN; no order; no allocation; no webhook
  (tails and a complete Workers Logs query agree). The window ended with `manual-stop`; restore gate and post-check PASSED.
- The original evidence file's `requests_sent: 4` / `peak_in_flight: 4` counted send attempts. They are not concurrency evidence.
- Cause: the four connections were opened at start and sat idle about 26 s waiting for `--fire-at`. Connections through this
  container's proxy survived 10 s idle and were dead at 20 s (read-only test; the exact limit was not measured).

### Offline repair — 2026-10-05

Status: **Repo** (`/qa/b3-stress-harness/b3stress/fire.py`, `FIRE.md`). This repaired script (sha256 `a68efef2…`) is the one that passed S4.

- Transport invariant: no mutation connection exists during a wait. Connections are opened `PRE_LEAD_S` (3 s) before the release, each
  is probed read-only on the same connection, and the release is aborted before any mutation if any probe fails, any connection is
  older than 5 s or idle longer than 4 s, or the release would slip more than 2 s.
- Evidence semantics: attempts, sends, HTTP responses, Shopify request ids, mutation responses and confirmed deliveries are counted
  separately; only a Shopify request id confirms delivery; the concurrency verdict is `NOT_PROVEN` unless four distinct request ids
  arrive for overlapping requests, and even then it is client-side only.
- The QAI fixture is retired in code (`RETIRED_COMPETITIONS`); its one-shot marker is untouched; `install.sh` now refuses to reinstall
  over a state directory that holds a marker. A rerun needs a **fresh** fixture, a reviewed update of the pinned constants, a fresh
  state directory and separate approval.
- Tests: `firetest.py` 100/100 at the time, 102/102 now (reproduces the failure with a simulated idle expiry: the old architecture fails 4/4, the repaired one
  succeeds 4/4 under 26 s, 40 s and 600 s waits); mutation check 67/67 killed (36 of `fire.py`).

### Real-proxy dry rehearsal and QAJ fixture — 2026-10-05

- Three read-only `fire.py dry --fire-at` rehearsals through the real proxy (40 s wait each): all 4 probes OK every time; connection
  age at release 2.47-2.60 s (limit 5), idle 2.25-2.37 s (limit 4), open + probe 0.69-0.75 s of the 3 s lead, release slip 0-1 ms,
  0 errors, 0 mutations built or sent. This proves connection timing only, not concurrency.
- Fresh fixture QAJ: product 15915106926966 (unpublished, 6 tracked/DENY, `skill_mode=none`), drafts #D32-#D35 (1, 1, 2, 2) OPEN,
  QA D1 QAJ1001-QAJ1006 AVAILABLE (exact 7-row registration), `fire.py` and `stress.json` pinned. Fired once on 5 Oct: S4 PASS (above).

### Method (QA Stress Driver)

- Shopify app **Greenside QA Stress Driver**, `write_draft_orders` only, QA use only. Credentials are environment variables
  (`runbooks/credentials.md`), never printed, logged or committed.
- `fire.py`: after all waiting, four threads each open a fresh connection about 3 s before the release, probe it read-only, then
  send one `draftOrderComplete` together from one barrier; no retries; one-shot marker per fixture. Every request's timing and
  request id is recorded. Details: `/qa/b3-stress-harness/b3stress/FIRE.md`.
- Concurrent requests do not guarantee overlapping webhooks; the `overlap.py` rule above is what decides a PASS.
- Each live run needs a fresh fixture and separate explicit approval. QAH (A5), QAI (failed attempt) and QAJ (S4) are all spent.
