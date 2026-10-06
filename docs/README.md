# Greenside documentation

`CLAUDE.md` (repository root) is the authoritative short briefing. These documents hold the
detail it points to.

## Evidence labels

Every factual statement in these documents carries one of these labels:

| Label | Meaning |
|---|---|
| **Repo** | Verifiable in this repository; the branch and commit are named |
| **Verified live (date)** | Checked read-only against Shopify or Cloudflare on that date |
| **Historical / uncommitted** | Recorded in earlier sessions; the evidence is not in the repository |

Re-verify anything labelled "Verified live" or "Historical" before relying on it.

## Paths

Paths in these documents are relative to `docs/`. Paths starting with `/` are relative to the
repository root (for example `/qa/b3-stress-harness/`).

## Index

| Document | Contents |
|---|---|
| `operating-layer.md` | Capability matrix, `gs` read-only command layer, safety contract, procedures, gaps |
| `architecture/allocator.md` | Entry allocator: responsibilities, flow, rules, environments |
| `production-readiness.md` | What must be true before the production allocator goes live |
| `runbooks/qa-live-window.md` | How a QA live window is opened, guarded and restored |
| `runbooks/credentials.md` | Credential inventory (names and scopes only) and handling rules |
| `qa/concurrency-testing.md` | Concurrency: **S4 PASS (5 Oct 2026)**, its scope and limits; history (A5, failed first attempt, repair) |
| `qa/l1-load-test.md` | L1 load test design v2 (approved 6 Oct 2026): 750 orders, staircase, webhook safety envelope. Stage 1 (offline build) only |
| `qa/evidence-and-observability.md` | Tail, heartbeat and Workers Logs evidence model |
| `/qa/b3-stress-harness/README.md` | B3 stress harness: install, layout, the S4 fire script (`b3stress/FIRE.md`) |
| `/tools/gs/` | `gs` read-only operating CLI (`gs.py`), `policy.json`, tests (`test_gs.py`), mutation check (`mutation_check.py`); see `operating-layer.md` |
| `open-items.md` | Open decisions, follow-ups and known gaps |
| `allocator-qa/SW1-SW3-test-plan.md` | Sweep-recovery QA plan (SW1–SW3) |
| `history/CLAUDE-2026-09-14.md` | Verbatim copy of the previous `CLAUDE.md` (theme and Klaviyo era); historical |
