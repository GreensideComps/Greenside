# L1 load-test harness

Design: `docs/qa/l1-load-test.md` (L1 v2, approved 6 Oct 2026). **Stage 1 only: offline build and tests.** Nothing here has run
against Shopify, D1 or the Worker. Every live command refuses without its own approval phrase.

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
| `guardl.py`, `guardl.sh` | load-window guard: schedules, allow-list, drain deadlines, fail-safe restore | Stage 4 |
| `shopsnapl.py` | post-drain Shopify export by tag, refuses while the window is open | after Stage 4 |
| `loadmetrics.py` | throughput / stability metrics, each OBSERVED or DERIVED | offline |
| `loadrecon.py`, `recon.sql` | independent reconciliation (Python over exports + read-only SQL) | offline / read-only |
| `manifest.py` | deterministic MANIFEST.sha256 | offline |
| `rehearse.py` | read-only proxy and clamp rehearsals | approved rehearsal stage |
| `sim.py` | offline World model (leaky-bucket allocator) used by tests and `load.py simulate` | none |

## Tests

    qa/l1-load-harness/run-tests.sh              # unit tests, syntax, secret scan
    qa/l1-load-harness/run-tests.sh --mutation   # + mutation check (tests/mutation_l1.py)
    python3 qa/l1-load-harness/l1/load.py simulate --out /tmp/l1sim --cost 28   # offline governor run against the model

Unit tests are named after the Stage 1 requirements (R01–R38). The D1 tests build a SQLite database from the allocator's own
migrations (`git show e917bb5:greenside-entry-allocator/migrations/...`) and snapshot it with `b3stress/snap.sql`.
