# Fire script (`fire.py`)

Four concurrent `draftOrderComplete` calls for one pinned four-draft fixture. Self-test: `b3stress-selftest-cmds/firetest.py`
(102 checks, run by `run-all.sh`); mutation check: `tools/gs/mutation_check.py` (36 deliberate breakages of `fire.py`, all must fail the
self-test).

**Current state: S4 PASS on 5 Oct 2026 with this exact script (sha256 `a68efef2…`) on the QAJ fixture** (product 15915106926966,
drafts #D32-#D35, confirmation phrase `FIRE-QAJ-D32-D35-4-COMPLETIONS`). Result and evidence: `docs/qa/concurrency-testing.md`,
`../evidence/s4-2026-10-05-qaj/`. QAJ is now spent (its one-shot marker exists in its state directory); QAI (#D28-#D31) is retired after
the failed attempt of 30 Sep (`../evidence/s4-2026-09-30-failed/`). Another live run needs a new fixture pinned in a reviewed change, a
fresh state directory and separate explicit approval.

## The 30 Sep failure and the transport invariant

The first live attempt opened its four connections at start, then waited about 26 s for `--fire-at`. Connections through this
container's proxy die when idle: a read-only test held connections idle for 2, 10, 20, 26 and 35 s; 2 and 10 s worked, 20 s and longer
gave `SSLEOFError`. All four sends failed about 1 ms after send; nothing reached Shopify. Verdict: **FAIL, execution transport
failure; no mutation delivered; no allocator execution; concurrency unproven.**

Invariant enforced now (constants in `fire.py`, checked by `firetest.py`):

| Constant | Value | Meaning |
|---|---|---|
| `PROXY_IDLE_OBSERVED_SURVIVED_S` / `_DEAD_S` | 10 / 20 | The measured idle bounds. The exact limit in between was not measured |
| `PRE_LEAD_S` | 3.0 | Mutation connections are opened this long before the release, never earlier. All longer waiting happens first, with no connection in existence |
| `MAX_CONN_AGE_S` | 5.0 | Connection age (connect to release) above this aborts the fire |
| `MAX_CONN_IDLE_S` | 4.0 | Idle since the connection's last completed request (the probe) above this aborts the fire. Must be at most half the 10 s observed to survive |
| `MAX_SLIP_S` | 2.0 | If opening and probing push the release more than 2 s past `--fire-at`, abort |

## Sequence

1. Refusals before any network: `--confirm` phrase, retired fixture, existing one-shot marker, `--fire-at` outside [-2 s, +30 min].
2. Read-only preconditions (short-lived connections): `stress.json` matches the pinned fixture; token scope exactly `write_draft_orders`;
   app, shop and effective scopes; `draftOrderComplete` signature; each draft OPEN with the expected id, name, quantity, tags, title,
   currency, total; the fixture-tagged drafts among the latest 10 (the script's message text still says "QAI"; cosmetic) are exactly the four. Live only: live gates (production Worker absent,
   QA Worker on the single 100% gate-passed version with `DRY_RUN` `"false"`, `gate.txt` PASSED, arming 0-1200 s old, guard alive,
   no `manual-stop`).
3. Wait until `--fire-at - PRE_LEAD_S`. No mutation connection exists during this wait.
4. Each of four worker threads opens its own connection and sends one read-only probe (`draftOrder(id) { id name status }`) on that
   **same** connection. The probe must return HTTP 200, a Shopify request id, the right draft, status OPEN.
5. All threads meet at barrier `ready`. Main aborts the whole fire (exit 2, nothing sent) if any connection or probe failed, if the
   release would slip more than `MAX_SLIP_S`, or if any connection exceeds `MAX_CONN_AGE_S` / `MAX_CONN_IDLE_S` at the release.
   A failed connection is never reused, reopened or re-probed.
6. Main creates `fire-<competition>.done` with `O_EXCL` (if it already exists: abort), then releases barrier `release`; the four
   mutations go out together on the probed connections. No retries, no stagger.

## Evidence semantics (`--out FILE`)

Per request: `attempt_started`, `connection_established`, `probe_ok` (+ probe status and request id), `send_started`, `send_completed`,
`http_response_received`, `shopify_request_id`, `mutation_response_received`, `delivery_confirmed`, result, connection age and idle at
release, and all timestamps.

- **Delivery is confirmed only by a Shopify request id (`x-request-id`) on an HTTP response.** A send that fails (SSLEOF, timeout) or a
  response without a request id is never a delivery.
- `evidence_summary` counts each stage separately. `client_overlap_attempts_DIAGNOSTIC_ONLY` is never evidence.
  `client_overlap_confirmed` uses confirmed deliveries only.
- `concurrency_verdict` is `NOT_PROVEN` unless all four deliveries are confirmed with four distinct request ids and overlapping intervals;
  even then it is only `CLIENT_SIDE_DELIVERY_OVERLAP_ONLY`. Server-side concurrency needs `overlap.py` (cross-order webhook overlap) and
  the four orders.
- `fire.py evaluate --evidence FILE` re-reads any evidence file offline, including the old-schema 30 Sep file, which evaluates to
  4 attempts, 0 confirmed deliveries, 0 request ids, NOT_PROVEN.

Exit codes: 0 all four delivered and successful; 2 refused or aborted before any mutation; 3 mutations released and at least one not
confirmed or not successful ("DO NOT RE-RUN": inspect Shopify and D1 first).

## Fixed facts

| Item | Value |
|---|---|
| Store (constant) | `7r5csb-1j.myshopify.com`. QA and production share it |
| Endpoint | `POST /admin/api/2025-10/graphql.json`; token via `POST /admin/oauth/access_token` (client credentials) |
| App and scope | Greenside QA Stress Driver, `write_draft_orders` (+ implied `read_draft_orders`) |
| Credentials | `QA_STRESS_SHOPIFY_CLIENT_ID`, `QA_STRESS_SHOPIFY_CLIENT_SECRET` (names only); token in memory only |
| Payload | `{"query": "mutation CompleteQaiDraft($id: ID!) { draftOrderComplete(id: $id) { draftOrder { id name status } userErrors { field message } } }", "variables": {"id": "<draft GID>"}}` |
| `paymentPending` | Deprecated, default `false`, omitted. Payment status when `paymentGatewayId` is omitted is not documented by Shopify; A5's orders were PAID |
| Proven by S4 | this app's token completes drafts; the resulting orders were PAID (manual gateway) |
