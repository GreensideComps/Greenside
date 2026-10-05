# S4 live fire, 30 Sep 2026: FAIL, execution transport failure (preserved evidence)

**Do not edit, rename or delete anything in this directory.** `MANIFEST.sha256` lists checksums of every file; verify with
`sha256sum -c MANIFEST.sha256`. The files are copies taken on 5 Oct 2026 from the session scratchpad of the 30 Sep run, unmodified
(`fire.py.as-run` is the script exactly as run; its SHA-256 matches `script_sha256` in `fire-live-evidence.json`).

## What happened (times UTC; add 1 h for BST)

| Time | Event | Evidence |
|---|---|---|
| 10:07:39 | Monitoring started by the full `goliveb.sh` | `goliveb-full.log` |
| 10:31:42 | Gate PASSED: QA Worker `ad67fcf2`, `DRY_RUN=false`; guard started | `gate-run.log`, `livegate.log`, `guard.log` |
| 10:31:50 | Armed, READY FOR GO | `qag-start`, `goliveb-full.log` |
| 10:44:24 | `fire.py live` started; four connections opened here | `fire-live-evidence.json` |
| 10:44:49 | One-shot marker written | `marker-fire-15905304379766.done.copy` |
| 10:44:50 | Release. Four sends failed with `SSLEOFError` about 1 ms after send; no response, no request id | `fire-live-evidence.json` |
| 10:45:40 | Window ended with `manual-stop`; strict restore to `DRY_RUN=true`, version `6b7dbf38` | `guard.log`, `restore.log`, `restoregate.log` |
| 10:50:44 | Post-check PASSED; D1 identical to the last live snapshot, integrity 0 | `postcheck.json`, `d1-final.json`, `d1-post.json` |

## Correct verdict

**FAIL, execution transport failure. No mutation was delivered to Shopify. No allocator execution occurred. Concurrency remains unproven.**

- Attempts: 4. Confirmed deliveries: 0. Shopify request ids received: 0. HTTP responses received: 0.
- Drafts #D28-#D31 stayed OPEN, no order was created, no entry allocated, no webhook seen (tails and a complete Workers Logs query
  showed only `/health` probes and one cron sweep). `overlap.json` verdict SEQUENTIAL, 0 hooks.
- The `overlap` block inside `fire-live-evidence.json` (`requests_sent: 4`, `peak_in_flight: 4`, `genuine_overlap: true`) counted send
  **attempts**. It is not evidence of concurrency and must never be quoted as such. `fire.py evaluate` re-reads this file with the
  corrected semantics.

## Root cause

`fire.py` opened its four connections at the start of the run and then waited about 26 s for `--fire-at`. A read-only experiment on
30 Sep (connections through this container's proxy held idle for 2, 10, 20, 26 and 35 s, then one harmless read) gave HTTP 200 at 2 s
and 10 s and `SSLEOFError` at 20, 26 and 35 s. The idle limit therefore lies between 10 and 20 s. The exact value was not measured.
The offline tests did not catch it because their fake connections never expired.

## Fixture

The QAI fixture (product 15905304379766, drafts #D28-#D31) and its one-shot marker are retired. A rerun uses a fresh competition
fixture and fresh one-shot state; the marker must not be deleted, reset or bypassed.
