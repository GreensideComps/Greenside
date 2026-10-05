# Greenside Competitions — project briefing

UK golf prize-competition (prize-draw) business on Shopify, pre-launch.
Store: `7r5csb-1j.myshopify.com` (greensidecompetitions.com).

This is the authoritative briefing. Detail lives in `docs/` (start at `docs/README.md`).
If this file and verified reality disagree, trust the verified reality, stop, and report the
discrepancy. The previous briefing is kept in `docs/history/CLAUDE-2026-09-14.md`.

## Non-negotiable rules

1. **Never guess.** Inspect and verify the current state (repository, Shopify, Cloudflare, D1)
   before acting or claiming anything. Report exactly what the evidence shows.
2. **Production changes need explicit approval**, every time, for that specific action: Worker
   deploys, secrets, webhook registration, theme publishing, real products, orders or inventory.
3. **QA and production share the SAME Shopify store.** QA products and QA orders live in the
   live store, so every Shopify write affects production. QA/production separation exists only
   at the Cloudflare Worker and D1 level.
4. **`DRY_RUN=true` is the safe default.** Only the exact string `"false"` enables writes. A live
   QA window runs only through the approved procedure (`docs/runbooks/qa-live-window.md`) and
   always ends restored to `DRY_RUN=true`.
5. **Every QA Wrangler command includes `--env qa`.** Without it, Wrangler targets the
   production Worker (`greenside-entry-allocator`).
6. **Secrets are never printed, logged, committed, stored in memory or included in evidence.**
   Refer to environment variable NAMES only (`docs/runbooks/credentials.md`). If a secret is
   exposed, say so and flag it for rotation.
7. **Shopify app scopes are fixed:**
   - Greenside Entry Allocator: `read_orders`, `read_products` only. The Worker refuses a
     token carrying `write_inventory`.
   - Greenside QA Stress Driver: `write_draft_orders` only. QA concurrency testing only; never
     used for production.
8. **Report results as evidenced.** A5 was INCONCLUSIVE as a genuine concurrency proof: the
   Shopify connector serialised the supposedly parallel calls. Do not describe it as a
   successful concurrency test. The concurrency proof is S4 (PASS, 5 Oct 2026): one six-entry
   competition, four concurrent completions, a correctness proof, not a load test
   (`docs/qa/concurrency-testing.md`).

## Architecture (high level)

- **Theme**: Horizon-based Online Store 2.0 theme, mirrored in this repository. Agents can only
  stage changes on unpublished themes; publishing is done by hand in Shopify admin.
- **Entry allocator** (`greenside-entry-allocator/`): Cloudflare Worker + D1. Shopify order
  webhooks plus scheduled reconciliation sweeps allocate sequential, per-competition entry
  numbers and handle refunds, cancellations and freeze. It does not draw winners and has no
  inventory write. `docs/architecture/allocator.md` is the authoritative record of where the
  current implementation lives (branch and commit). Do not assume the branch holding the
  allocator code is the correct working branch for a session.
- **Payment gate**: an order is allocated only when it is PAID, not cancelled, not a test
  order, and the competition is OPEN.
- **Skill question removed for launch**: every launch competition product must have
  `custom.skill_mode = none` set BEFORE it opens, and it must never change afterwards. Unset
  means the legacy "question required" mode.
- **`greenside-competition-closer/` is RETIRED** (it writes inventory). Never deploy it.

## Current state (high level; re-verify before relying on it)

- Deployed: only the QA Worker `greenside-entry-allocator-qa` (DRY_RUN=true) and QA D1
  `greenside_entries_qa`. Verified read-only on 2026-10-05 (`gs verify`).
- The production allocator is not deployed, and its production D1 is not provisioned.
- Concurrency: **S4 PASS (5 Oct 2026, QA)**. Four concurrent Shopify completions → four PAID orders → four overlapping
  allocating Worker executions → QAJ1001–QAJ1006 each allocated exactly once, integrity 0. Evidence:
  `qa/b3-stress-harness/evidence/s4-2026-10-05-qaj/` (immutable). QA fixtures QAH, QAI, QAJ are spent; cleanup needs approval.
- Launch readiness and open items: `docs/production-readiness.md`, `docs/open-items.md`.

## Branch and commit discipline

- Work only on the branch named for the session. Commit and push only when asked. No pull
  requests unless asked. Never force-push, merge branches or change the default branch without
  explicit approval.
- The repository currently has no single integration branch; theme, allocator, QA and docs may
  live on different branches. Check the current branch and repository state before assuming
  where a change belongs.

## Operating layer (read-only, verify first)

`python3 tools/gs/gs.py verify` checks live state against `tools/gs/policy.json` (Workers, D1, both Shopify apps' scopes,
webhooks); `status` and `d1 query "SELECT ..."` inspect it. `gs` cannot write. Capability matrix, gaps, safety contract for future
write commands and recovery notes: `docs/operating-layer.md`. Run `gs verify` at the start of any operational session.

## Where things are

| Need | Location |
|---|---|
| Documentation index | `docs/README.md` |
| What Claude can see and do; `gs` commands; gaps | `docs/operating-layer.md` |
| Allocator architecture | `docs/architecture/allocator.md` (+ the allocator README on its branch) |
| Production readiness checklist | `docs/production-readiness.md` |
| QA live-window procedure | `docs/runbooks/qa-live-window.md` |
| Credentials (names and scopes only) | `docs/runbooks/credentials.md` |
| Concurrency testing | `docs/qa/concurrency-testing.md` |
| Evidence and observability | `docs/qa/evidence-and-observability.md` |
| B3 stress harness | `qa/b3-stress-harness/README.md` |
| Sweep-recovery QA plan (SW1–SW3) | `docs/allocator-qa/SW1-SW3-test-plan.md` |
| Open items | `docs/open-items.md` |

## Working agreements

Reports as copy-paste code blocks; times in UK time (BST in summer). Prefer simple and
reliable. Separate MUST FIX BEFORE LAUNCH from CAN IMPROVE AFTER LAUNCH. Customer-facing copy
stays honest about pre-launch status: no invented winners, testimonials or counts.
