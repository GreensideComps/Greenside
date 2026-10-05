# Greenside operating layer: what Claude can see and do

Written 2026-09-30 from a read-only audit; updated 2026-10-05 after S4 PASS (test counts, Stress Driver, QA fixtures). Every fact carries an evidence label from `docs/README.md`.
**Verified live (2026-09-30)** means checked that day with the tool named. Re-verify before relying on it: run `python3 tools/gs/gs.py verify`.

## 1. The systems

| System | Environment | State | Evidence |
|---|---|---|---|
| Shopify store `7r5csb-1j.myshopify.com` (greensidecompetitions.com, Basic plan, GBP) | **One store shared by QA and production.** Every Shopify write is a production write | Live; MAIN theme is "Greenside Visual Fixes (Draft)" | Verified live (2026-09-30) |
| Cloudflare Worker `greenside-entry-allocator-qa` (`DRY_RUN=true` by default, crons `*/15`, `20,50`) | QA | Only Worker in the account | Verified live (2026-09-30) |
| Cloudflare Worker `greenside-entry-allocator` | Production | **Absent** | Verified live (2026-09-30) |
| D1 `greenside_entries_qa` | QA | Only D1 in the account; competitions QAE..QAJ and PUT (7; all QA fixtures, all spent) | Verified live (2026-10-05) |
| Klaviyo account (id `VLkYWr`, "Greenside competitions", GBP) | Production | Connected through a connector | Verified live (2026-09-30) |
| Theme | Mirrored in this repo; agents may stage only on unpublished themes; publishing is manual | | `CLAUDE.md` |

QA and production separate only at the Worker and D1 level. There is no QA Shopify store.

## 2. Capability matrix

Categories: **A** already excellent, **B** usable but should be improved, **C** possible with existing infrastructure, **D** needs a new integration or tool, **E** not currently practical.
R/W = read or write. "Risk" is the harm if misused.

| # | Capability | Cat | How Claude accesses it | R/W and scope | Risk | Limitation | Better with | Cost | Value |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Git / repo | A | git CLI; GitHub connector | R/W, repo scope only | Low | No integration branch: theme, allocator, QA and docs live on different branches. No CI in any inspected branch | One integration branch; CI running `gs test` | M | H |
| 2 | Claude configuration | **D** | Repo `.claude/` | None on the working branch. Branch `epic-gauss` has a `.claude/settings.json` that pre-approves `graphql_mutation`, `create-product`, `update-product` and other Shopify write tools, with no deny rules | **High** | No project permissions or hooks; only the platform's default stop hook exists | Add deny rules (`gs audit claude-config` lists them). Needs your approval | L | H |
| 3 | Permissions and hooks | B | Platform defaults | The stop hook forces a commit prompt. Nothing Greenside-specific | Med | Safety rests on CLAUDE.md wording and the harness scripts | Deny rules; a pre-write hook | L | H |
| 4 | Environment variables | A | Session env | Names only (`gs audit credentials`) | Low | `AWS_*` and `CLOUDSDK_AUTH_ACCESS_TOKEN` names are present. Origin unverified; nothing in this repo uses them | Owner confirms and removes | L | M |
| 5 | Shopify Admin API (connector) | B | MCP connector "Shopify Claude Connector App" | R/W. Scopes include `write_orders`, `write_products`, `write_inventory`, `write_themes`, `write_draft_orders`, `write_customers`, `write_discounts`, `read_all_orders`, `read_reports`, `read_analytics`. Live store | **High** | Far broader than any Greenside rule needs. The connector's own policy blocks some operations (e.g. `publishableUnpublish` was refused on 2026-09-30) | Narrow the app's scopes; needs your approval | L | H |
| 6 | Shopify Admin API (Allocator app) | A | Client-credentials token from env | R, `read_orders` + `read_products` only | Low | Cannot read publications, inventory or draft orders | none | none | H |
| 7 | Shopify Admin API (QA Stress Driver) | A | Client-credentials from env | W, `write_draft_orders` (+ implied read) | Med | Cannot read `product`/`variant`/`order` fields. Proven by S4 (5 Oct 2026) to complete drafts; the orders were PAID | none | none | M |
| 8 | Shopify webhooks | A | `webhookSubscriptions` via the Allocator token | R. 5 topics (`ORDERS_CREATE/PAID/CANCELLED/EDITED`, `REFUNDS_CREATE`), all to the QA Worker | Low | Cannot see delivery history or failures | Delivery-failure surfacing | M | H |
| 9 | Storefront | E | none | none | none | No storefront token and no synthetic visitor. Browser tooling exists (row 17) but no journey tests for the store | Storefront API token plus browser journeys | M | H |
| 10 | Cloudflare Workers | B | Env token via wrangler (needs a worktree + `npm ci`), REST GETs, MCP connector | R/W QA. Production absent | High (writes) | wrangler is not installed globally | `gs cloudflare` wrappers (future) | M | H |
| 11 | Cloudflare D1 | **A** (read) | `gs d1 query` (SELECT only), harness `dsnap.sh`, MCP connector | R via `gs`; the connector and wrangler can also write | High if unguarded | The D1 REST API accepts writes with a D1 Write token, so the guard is `gs`'s own | none | done | H |
| 12 | Workers Logs | A | `b3obs/obsq.py` (read-only) | R, QA Worker; needs "Metadata Read-Only" | Low | ~0.47% of invocations never stored; 8-28 s delay (`docs/qa/evidence-and-observability.md`) | none | none | M |
| 13 | Deployment | B | `gate.sh` / `goliveb.sh` (QA only) | W QA | High | No production deploy path exists, by design (`production-readiness.md`) | Production runbook plus approval workflow | M | H |
| 14 | Rollback | B | `restore.sh` + `postcheck.sh` (QA) | W QA | Med | QA only. D1 Time Travel exists (30 days paid / 7 days free, restore is **destructive and in place**; verified in Cloudflare docs 2026-09-30) but is not wired into any Greenside procedure | Documented, tested D1 recovery procedure | M | H |
| 15 | Browser / UI | C | Playwright 1.x CLI plus Chromium in `/opt/pw-browsers` | R | Low | Python `playwright` package absent. Only test file: theme signup modal, on branch `greenside-network-access-nsrgnf` | Journey tests against a preview theme | M | H |
| 16 | File / library access | B | Repo, scratchpad | R/W | Low | `gs`, harness and theme all live in one repo tree | none | none | M |
| 17 | Analytics | C | Connector `run-analytics-query` (ShopifyQL); Klaviyo metric tools | R | Low | Not exercised in this audit | A `gs analytics` wrapper with saved queries | M | H |
| 18 | Klaviyo | B | MCP connector | R/W. Includes `send_campaign`, `create_flow`, subscribe/suppress | **High** | Read verified (account). No send policy or dry-run | Deny `send_campaign` unless approved | L | H |
| 19 | Email | C | Microsoft 365 connector (`outlook_send_mail`, drafts) | R/W | Med | Presence of the tools only; not tested here | Draft-only policy | L | M |
| 20 | Scheduling | C | Session `CronCreate` / `ScheduleWakeup`; Claude Code Remote routines. One unrelated disabled routine exists (another business) | W | Med | Nothing schedules `gs verify`; routines do not persist state on their own | Routine that runs `gs verify` and reports | M | H |
| 21 | Image / video | E | Figma connector; no system ffmpeg/imagemagick | R/W in Figma | Low | Not needed for operations | | | L |
| 22 | Testing | A | Allocator Vitest (21 files, 365 tests, all pass; `tsc --noEmit` clean; run in a separate copy of the `epic-gauss` code, verified 2026-10-05); harness suites (216); `gs` tests (85) | R | Low | Not in any CI | CI | M | H |
| 23 | Mutation testing | B | `tools/gs/mutation_check.py`: 67 deliberate breakages (31 of `gs`, 36 of `fire.py`, including old-architecture restoration, freshness gates, SSLEOF-as-delivery, attempts-as-evidence); 67/67 killed (2026-10-05) | R | Low | Not applied to the allocator source (365 tests unmutated) or to the rest of the harness | Extend to the allocator | M | M |
| 24 | Concurrency testing | A | B3 harness plus `fire.py` | W QA | Med | **S4 PASS (2026-10-05)**: four concurrent completions, overlapping Worker executions, correct allocation. Scope: one six-entry competition; not a load test. Each further run needs a fresh fixture and approval | Load testing, if ever needed | M | H |
| 25 | Synthetic customers | C | QA fixtures (QAI, QAJ products and drafts) made via connector and Stress Driver | W | Med | Fixtures are hand-built per test; no generator | A fixture builder with dry-run and audit | M | H |
| 26 | Monitoring | B | Harness heartbeat, two tails and the guard, **only during a window** | R | Low | Nothing runs between windows | Scheduled `gs verify` | M | H |
| 27 | Alerting | E | `PushNotification` tool exists; nothing is wired | | | No alert on any condition | Scheduled `gs verify` plus notification on FAIL | M | H |
| 28 | Backup / recovery | E | See row 14 | | | No backup or export procedure exists | D1 export to R2, restore drill | M | H |
| 29 | Diagnostics | A | `gs status`, `gs verify`, `gs d1 query` | R | Low | Covers QA only (production absent) | Production checks once deployed | L | H |
| 30 | Business tooling | C | Shopify orders, Klaviyo flows/metrics via connectors | R/W | High (W) | No Greenside-specific reporting yet | `gs` reports | M | H |

## 3. Highest-leverage gaps

1. **Permissions.** No deny rules anywhere. The broad connector app (row 5) plus a branch that pre-approves Shopify mutations means a mistaken write reaches the live store. Fix needs your approval.
2. **No always-on verification.** State is only checked when a person or session asks. `gs verify` is the check; it is not scheduled.
3. **No alerting, no backup drill.** Rows 27, 28.
4. **No storefront / customer-journey test.** Row 9.
5. **Scattered code.** Theme, allocator, QA harness and docs are on separate branches. `docs/architecture/allocator.md` names the allocator commit.

## 4. Architecture: `gs`, one read-only command layer

Located in `tools/gs/`. Standard library only. Reuses the proxy transports in `qa/b3-stress-harness/b3stress/fire.py` and the SQL in `b3stress/snap.sql`.

| Command | What it does |
|---|---|
| `gs.py status [--json]` | Repo state, credential **names**, Cloudflare Workers/versions/DRY_RUN/crons, D1 pools and integrity, both Shopify apps' scopes, webhooks |
| `gs.py verify [--json]` | 14 invariants from `tools/gs/policy.json`. Exit 0 all PASS, 1 any FAIL, 2 could not judge. WARN never fails the run |
| `gs.py audit claude-config [--ref REF]` | Finds risky pre-approvals and missing deny rules in settings files or at a git ref |
| `gs.py audit credentials` | Present/absent names only |
| `gs.py d1 query "SELECT ..."` | One read-only statement on the QA D1 |
| `gs.py test` | `test_gs.py` plus the fire-script tests |

Boundaries enforced in code, and covered by tests:
- Shopify: only GraphQL documents that contain no mutation or subscription.
- D1: exactly one `SELECT` (or `WITH ... SELECT`). Comments and literals are tokenised in order. Any DML/DDL/PRAGMA/transaction word, second statement, unknown leading verb, or unterminated quote is refused before any request.
- Cloudflare: GET, plus the one D1 query POST behind the SQL guard.
- Audit log: one JSON line per invocation (`GS_AUDIT_LOG`, default `~/.greenside/gs-audit.jsonl`): time, command words, exit code, SQL length. It never holds SQL text, arguments' values, outputs, or tokens.
- `policy.json` is the definition of "expected". Changing it is a safety change: review it like a gate.

**Contract for any future write command** (none exist yet; a write command must not be added without your approval):
1. `--dry-run` is the default. A live run needs an explicit confirmation phrase and, where the risk is high, a named approval.
2. Input validation with an allow-list of targets, pinned to IDs (see `fire.py`).
3. The narrowest credential that can do it (dedicated app, not the connector).
4. An audit-log line before and after.
5. Post-action verification by a different code path, and a documented rollback.
6. Refusal in production unless a production runbook and an explicit approval exist.

## 5. Operating procedures

**What Claude may do without asking:** everything in the `gs` table, the harness's read-only checks, `fire.py validate` / `dry`, and offline tests.
**What needs explicit approval, every time** (CLAUDE.md rule 2 and 3): any Shopify write (all writes hit the live store), any D1 write, any Worker deploy, secrets, webhooks, theme publishing, credential or scope change, live windows, `fire.py live`, commits and pushes.

- **Diagnose:** `python3 tools/gs/gs.py verify`, then `status`, then `d1 query` for detail. During a live window `cf.qa_dry_run_true` FAILs by design.
- **Test:** `python3 tools/gs/test_gs.py` (85 checks), `python3 tools/gs/mutation_check.py` (67 mutants, about 2 minutes), the harness suite `qa/b3-stress-harness/README.md` (216 offline checks after install into a fresh directory; the opt-in live Workers Logs check is `b3stress-selftest-cmds/wlreal_recent.py`), the allocator's own `npm test` (365 tests in 21 files) **in a separate copy** (running it inside the harness worktree breaks the clean-tree gate).
- **Verify a QA deployment:** `gs verify` (single 100% version, `DRY_RUN`, crons); the harness's restore gate and `postcheck.sh` for the sweep evidence (`docs/runbooks/qa-live-window.md`).
- **Recover:** QA Worker: `restore.sh` then `postcheck.sh`. D1: Time Travel (`wrangler d1 time-travel info|restore`) is destructive and in place; it has never been drilled here. Treat any restore as needing approval.

## 6. Known limitations and what would need your approval

- Not inspected: storefront behaviour, Klaviyo flows and campaigns, Microsoft 365 sending, analytics queries, Figma.
- `AWS_*` / `CLOUDSDK_AUTH_ACCESS_TOKEN` names in the environment: confirm the origin.
- Approvals needed for: adding deny rules to Claude's permission settings; narrowing the connector app's scopes; any new credential (e.g. a storefront token, an alerting webhook); a persisted schedule; a production Worker or D1; a CI provider.
