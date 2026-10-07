# L1 load-test harness

Design: `docs/qa/l1-load-test.md` (L1 v2, approved 6 Oct 2026; amendment A1 of 7 Oct 2026). Live so far: the Stage 2 canary
(`evidence/canary-2026-10-06/`), the Stage 3 fixture (`evidence/stage3-2026-10-07/`) and one Stage 4 run that stopped after 8 of
750 orders (`evidence/stage4-2026-10-07/`). Every live command refuses without its own approval phrase.

## Run 2 on the residual fixture (amendment A1, 7 Oct 2026; offline-tested, NOT run)

Run 2 completes the 742 drafts Stage 4 left OPEN (rows 9-750 of the SAME bound run-1 plan: unchanged indexes, quantities, tags
and draft GIDs; 1,637 units) against the QAL pool as Stage 4 left it. The 8 Stage 4 orders (#1030-#1037, drafts #D37-#D44)
holding QAL1001-QAL1013 are the run-2 baseline: pinned in `common.py` by exact ID, required present and unchanged, never re-sent,
excluded from the population by exact GID only (like the canary). New numbers must follow contiguously from QAL1014.

    python3 l1/load.py plan-run2 --run1-plan evidence/stage3-2026-10-07/plan-bound.json --out PLAN-RUN2.json   # offline
    l1/window.sh prelive S PLAN-RUN2.json PLAN_SHA                        # read-only; nothing deployed
    l1/window.sh live    S PLAN-RUN2.json PLAN_SHA LOAD-QAL-742-ORDERS-1637-ENTRIES

What changes with a run-2 plan (`"run": 2`, validated exactly against `RUN1_PLAN_SHA`, `RUN2_ROWS_SHA` and the pinned baseline):
the live phrase; the drafts-open count (742); the D1 reference rule (the exact baseline instead of a fresh pool); the governor
budget (the plan's 742 rows: A 50, B up to 525, C 167); the guard's drain point (baseline 13 + completed units); the
reconciliation (baseline excluded by exact ID and checked unchanged; numbers QAL1001..QAL(1013 + units)). Thresholds, staircase,
failwatch and the B3 mechanisms are unchanged; the allocator pin is the race-fix commit 610e189 for every run.

## Files (`l1/`)

| File | Role | Network |
|---|---|---|
| `common.py` | fixed facts, deterministic 750-row plan, QAL allow-list, approval phrases, envelope thresholds | none |
| `governor.py` | phase/rate state machine (A→B→C→E; T/T2 only if approved), WARNING / LOAD STOP / SAFETY STOP | none |
| `failwatch.py` | per-topic webhook failure counters (tail + Workers Logs), LOAD vs SAFETY | none |
| `load.py` | driver: gates, O_EXCL marker, pacing, one send per draft, evidence; `simulate` runs fully offline | Stage 4 |
| `sampler.py` | allocator bucket every 2 s, subscriptions every 30 s (the only allocator read during load) | Stage 2/4 |
| `clamp.py` | phase-T read-only bucket clamp (≤1.5 s windows, release on THROTTLED); needs the T phrase | approved T only |
| `stage.py` | creates the 750 £0 drafts (dry mode is offline) | Stage 3 |
| `canary.py` | the one canary order on an unregistered product | Stage 2 |
| `regsql.py`, `register.sh` | chunked QA D1 registration (< 90 KB per statement), exact-2000 verification | Stage 3 |
| `guardl.py`, `guardl.sh` | load-window guard: schedules, allow-list, drain deadlines, fail-safe restore; QA Worker and Workers Logs inputs fail closed | Stage 4 |
| `livewin.py` | Stage 4 wiring, read-only: QA Worker poller, Workers Logs poller, state files, state checks, restore verification, final failwatch | Stage 4 |
| `window.sh` | Stage 4 runbook: sequences the unchanged B3 tails, pre-live checkers, `gate.sh`, `restore.sh`, `postcheck.sh` with the L1 guard, pollers and driver | Stage 4 |
| `shopsnapl.py` | post-drain Shopify export by tag, refuses while the window is open | after Stage 4 |
| `loadmetrics.py` | throughput / stability metrics, each OBSERVED or DERIVED | offline |
| `loadrecon.py`, `recon.sql` | independent reconciliation (Python over exports + read-only SQL) | offline / read-only |
| `manifest.py` | deterministic MANIFEST.sha256 | offline |
| `rehearse.py` | read-only proxy and clamp rehearsals | approved rehearsal stage |
| `sim.py` | offline World model (leaky-bucket allocator) used by tests and `load.py simulate` | none |

## Stage 4 runbook (`l1/window.sh`; needs its own approval)

One state directory: `$S/b3stress`, the B3 harness installed by `window.sh install S` (the unchanged B3 `install.sh`, which
replaces `__SCRATCHPAD__` with S; refused over a used L1 state directory) with the allocator worktree at 610e189 (the
race fix; amendment A1) in `$S/b3qa`. The B3 scripts are used unchanged, so the tails, `gate.sh` and `restore.sh` write straight into it.

    l1/window.sh install S                                                # offline: B3 install.sh (resolves __SCRATCHPAD__ to S)
    git worktree add --detach S/b3qa 610e1899f352c09848c3bbc79630a0a9289d5658 && (cd S/b3qa/greenside-entry-allocator && npm ci)
    l1/window.sh prelive S PLAN-BOUND.json PLAN_SHA                       # read-only; nothing deployed
    l1/window.sh live    S PLAN-BOUND.json PLAN_SHA LOAD-QAL-750-ORDERS-1650-ENTRIES   # run 1; run 2: see above

`live`: pre-live again (install and worktree; QA Worker DRY_RUN=true on one version, no production Worker; tails and heartbeat;
`expected-subs.json`, `pre-max-order.json`, `d1-ref.json`, `drafts-open.json`; hh:01-04/31-34 slot; B3 strict sweeps and tail
continuity; no webhooks; D1 = reference; every plan draft OPEN: 750 in run 1, 742 in run 2) -> Workers Logs poller (`wl-poll.jsonl`, from `wl-from-ms.txt`) ->
`gate.sh` (`newver.txt`, `gate.txt`) -> `guardl-config.json` -> Worker poller (`worker-poll.jsonl`), sampler, `guardl.sh` -> guard
clear -> pre-GO (no webhooks, D1 unchanged) -> `qag-start` -> `check-state armed` -> `load.py live` -> wait for the guard's verified
restore -> `dsnap.sh d1-final.json` -> `postcheck.sh` -> `livewin.py final` (`failwatch-final.json`). The restore command for
both the guard and the driver is `window.sh restore S`: B3 `restore.sh` under a lock, skipped when the restore is already verified.
Any abort after the gate sets `manual-stop` (the guard restores; with no running guard the restore runs directly) and ends
the same way: verified restore, `d1-final.json`, `postcheck.sh`, final failwatch. A failed gate is restored by `gate.sh` itself.

Fail-closed inputs (guard): a missing, failed or stale QA Worker reading (> 30 s), a production Worker, a missing or unexpected
`DRY_RUN`, or a Worker off the gate-passed version is a SAFETY STOP; a failed delivery reported by Workers Logs feeds the existing
failwatch rules; an incomplete, gapped, malformed or stale (> 150 s) Workers Logs coverage is a LOAD STOP.

## Tests

    qa/l1-load-harness/run-tests.sh              # unit tests, syntax, secret scan
    qa/l1-load-harness/run-tests.sh --mutation   # + mutation check (tests/mutation_l1.py)
    python3 qa/l1-load-harness/l1/load.py simulate --out /tmp/l1sim --cost 28   # offline governor run against the model

Unit tests are named after the Stage 1 requirements (R01–R38); the Stage 4 wiring tests are `StageFour*` (W, L, S, R numbers);
the run-2 tests are `RunTwoTests` (A1_NN), including checks against the real Stage 3 plan and Stage 4 D1/order evidence. The D1
tests build a SQLite database from the allocator's own migrations (`git show 610e189:greenside-entry-allocator/migrations/...`,
identical to e917bb5's) and snapshot it with `b3stress/snap.sql`.
