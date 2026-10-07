# B3 allocator stress harness (QA only)

QA tooling for the Greenside entry allocator stress test (B3, QA store and QA Worker only).
It contains scripts and test fixtures only: **no credentials**. Every script reads
`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET`
from the environment and never prints them.

## Layout

| Path | Purpose |
| --- | --- |
| `b3stress/` | go-live, gate, guard, restore, postcheck, state checkers (`cstate`, `dstate`, `shopstate`), `overlap.py`, `coverage.py`, `evidence.py`, `fire.py` (S4 fire script, see `b3stress/FIRE.md`) |
| `b3stress/harness-v2-before/guard.sh` | the previous guard, used only by the before/after regression test |
| `b3obs/obsq.py` | read-only Workers Logs gap-recovery source used by `evidence.py` (tails stay primary) |
| `b3obs/wlfetch.py`, `ingest.py`, `q.sh` | validation-only Workers Logs tools |
| `b3stress-selftest-cmds/` | offline and read-only test suites; `run-all.sh` runs everything |
| `fixtures/b3live3/` | QA D1 and Shopify snapshots used by the stress self-test |
| `evidence/` | preserved run evidence, never edited, each with `MANIFEST.sha256`: `s4-2026-09-30-failed/` (failed S4 attempt), `s4-2026-10-05-qaj/` (S4 PASS) |
| `b3stress-replay/` | frozen tails and heartbeat around the real 28 Sep 16:13:45 BST tail blind spot |

## Install in a new session

```bash
S=<this session's scratchpad directory>
qa/b3-stress-harness/install.sh "$S"   # refuses a directory that holds a consumed fire-*.done marker
# allocator worktree used by gate/restore/supervisor (wrangler), at the approved commit:
git -C /home/user/Greenside worktree add --detach "$S/b3qa" 610e1899f352c09848c3bbc79630a0a9289d5658
(cd "$S/b3qa/greenside-entry-allocator" && npm ci)
bash "$S/b3stress-selftest-cmds/run-all.sh"   # expected 216/216 offline (49 + 5 + 4 + 43 Workers Logs --no-real + 9 + 4 + 102 fire.py)
python3 "$S/b3stress-selftest-cmds/wlreal_recent.py" "$S/b3obs"   # optional, read-only: live Workers Logs on a rolling 35-min window
```

`b3stress/stress.json` describes the QAJ stress fixture used by S4 (PASS, 5 Oct 2026; product 15915106926966, drafts #D32-#D35). QAJ, QAI (failed attempt) and QAH (A5) are all spent. A further live run needs a new fixture with its own approved setup, installed into a fresh directory (spent state directories hold consumed markers).
