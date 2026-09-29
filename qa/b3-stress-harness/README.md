# B3 allocator stress harness (QA only)

QA tooling for the Greenside entry allocator stress test (B3, QA store and QA Worker only).
It contains scripts and test fixtures only: **no credentials**. Every script reads
`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET`
from the environment and never prints them.

## Layout

| Path | Purpose |
| --- | --- |
| `b3stress/` | go-live, gate, guard, restore, postcheck, state checkers (`cstate`, `dstate`, `shopstate`), `overlap.py`, `coverage.py`, `evidence.py` |
| `b3obs/obsq.py` | read-only Workers Logs gap-recovery source used by `evidence.py` (tails stay primary) |
| `b3obs/wlfetch.py`, `ingest.py`, `q.sh` | validation-only Workers Logs tools |
| `b3stress-selftest-cmds/` | offline and read-only test suites; `run-all.sh` runs everything |
| `fixtures/b3live3/` | QA D1 and Shopify snapshots used by the stress self-test |
| `b3stress-replay/` | frozen tails and heartbeat around the real 28 Sep 16:13:45 BST tail blind spot |

## Install in a new session

```bash
S=<this session's scratchpad directory>
qa/b3-stress-harness/install.sh "$S"
# allocator worktree used by gate/restore/supervisor (wrangler), at the approved commit:
git -C /home/user/Greenside worktree add --detach "$S/b3qa" e917bb504a07bf19543555cd037281e1b9e47683
(cd "$S/b3qa/greenside-entry-allocator" && npm ci)
bash "$S/b3stress-selftest-cmds/run-all.sh"   # expected 121/121 (49 + 5 + 4 + 50 + 9 + 4)
```

`b3stress/stress.json` still describes the QAH stress product used in A5. A new stress product
needs a new, approved setup before any live run.
