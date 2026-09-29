# Concurrency testing

**Goal:** prove that when several orders for the same competition arrive at the same time, the
allocator still assigns every entry number exactly once, with no gaps or duplicates. A run
counts as a genuine concurrency proof only if `overlap.py` observes at least one cross-order
overlap: two webhook invocations for different orders in flight at the Worker at the same time.
With no such overlap, the run is INCONCLUSIVE, however correct its allocations are.

The harness is committed: `/qa/b3-stress-harness/` (Repo, this branch).

## A5 — 2026-09-29 — INCONCLUSIVE

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

## Next approach — QA Stress Driver (in progress)

- Shopify app **Greenside QA Stress Driver**, `write_draft_orders` only, QA use only.
  Credentials are supplied as environment variables (`runbooks/credentials.md`) and never
  printed, logged or committed.
- Mechanism: four independent HTTPS requests from four threads on pre-opened connections,
  released by one barrier, each completing one draft order. Every request's timing and
  request ID is recorded.
- Even genuinely concurrent requests do not guarantee overlapping webhooks (Shopify delivers
  them asynchronously), so a run can still be INCONCLUSIVE. The overlap rule is not relaxed.
- Steps, each needing separate approval: S1 read-only verification of the app and concurrent
  requests → S2 new isolated stress product and drafts → S3 fresh dry-run pre-live window →
  S4 live run with the unchanged harness.
