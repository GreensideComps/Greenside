# Evidence and observability (QA live windows)

Implementation: Repo — `/qa/b3-stress-harness/` (this branch). Measurements below are
**historical / uncommitted** (recorded 2026-09-28/29 on the QA Worker) unless stated otherwise.

## Primary evidence: redundant tails

- Two `wrangler tail` streams (`pre`, `pre2`) run in parallel, plus a confirmation tail during
  the gate. A heartbeat sends a tagged `/health` probe every 4 seconds.
- Continuity rule (`coverage.py`): every heartbeat probe must be seen by at least one tail;
  covered probes no more than 12 seconds apart.
- Tails were observed to drop out regularly, sometimes both at once; that is why a second,
  independent source was added.

## Tail-gap recovery: Workers Logs (read-only)

- Source: Cloudflare Workers Observability query API (`/qa/b3-stress-harness/b3obs/obsq.py`).
  It needs the Cloudflare token's Workers "Metadata Read-Only" role on the QA Worker.
- Used ONLY for a detected tail gap, never when the tails are continuous. Never overrides
  another abort condition.
- Rules, implemented in `evidence.py`:
  - no query before 60 seconds after the gap ends
  - the query must be complete: paged to the end, record count equals the series total,
    every `sampleInterval` = 1, nothing truncated
  - every heartbeat in the gap must be present
  - tails and Workers Logs must agree around the gap
  - recovered invocations go through the same checks as tail events
  - any failure means EVIDENCE LOST and a strict restore
- Measured behaviour (historical):
  - ingestion delay 8.5–28.4 seconds (313 samples)
  - records can appear out of order
  - about 0.47% of invocations are never stored
  - maximum 2000 records per page; `count` is not a total
  - paging with `offset` + `offsetDirection=next` is exact
- Limitation: Workers Logs cannot prove that no other invocation happened. A missing required
  webhook still fails the lifecycle checks and deadlines.

## Concurrency evidence

`overlap.py` classifies overlapping invocation intervals. A PASS requires at least one
cross-order overlap (`qa/concurrency-testing.md`).
