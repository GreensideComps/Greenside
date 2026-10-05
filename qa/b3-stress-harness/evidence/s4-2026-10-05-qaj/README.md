# S4 live fire on the QAJ fixture, 5 Oct 2026: PASS (all four layers evidenced)

**Do not edit, rename or delete anything here.** Verify with `sha256sum -c MANIFEST.sha256`. Copied unmodified from the session
scratchpad state directory (`qaj/b3stress`) right after the run; `fire.py.as-run` matches `script_sha256` in `fire-live-evidence.json`.
Times UTC (BST = UTC+1).

| Time | Event | Evidence |
|---|---|---|
| 08:59:37 | Monitoring started (full `goliveb.sh`, fresh state directory) | `goliveb.log` |
| 09:31:00 | Strict pre-live sweeps PASS; tail continuity 161/161; Shopify + D1 unchanged | `prestrict.json`, `coverage-prelive.json` |
| 09:31:51 | Gate PASSED: QA Worker `991799d3`, `DRY_RUN=false`; guard started | `gate-run.log`, `livegate.log` |
| 09:31:59 | Armed, READY FOR GO | `qag-start` |
| 09:44:50.000 | One live fire: four `draftOrderComplete` released from one barrier (send spread 1.2 ms) | `fire-live-evidence.json` |
| 09:44:51.7-51.9 | Four HTTP 200 responses, four distinct request ids, all COMPLETED | `fire-live-evidence.json` |
| 09:44:53.3-54.7 | Four `orders/paid` webhook executions overlap (common window 380 ms); each allocates | tails, `overlap.json`, `workers-logs-*.json`, `d1-*.json` |
| 09:44:59 | Pool complete: QAJ1001-QAJ1006 allocated once each | `guard.log`, `d1-complete.json` |
| 09:45:00 | Live */15 sweep after completion: all zeros | `guard.log` |
| 09:46:22 | Planned restore: `DRY_RUN=true`, version `d5710fab`; restore gate PASSED 09:47:14 | `restore.log`, `restoregate.log` |
| 09:51:08 | Post-check PASSED: clean dry-run deep sweep, D1 identical to last live snapshot, integrity 0 | `postcheck.json`, `d1-post.json` |

Draft → order → entries: #D32 → #1028 (qty 1) → QAJ1006; #D33 → #1026 (1) → QAJ1005; #D34 → #1025 (2) → QAJ1001-1002;
#D35 → #1027 (2) → QAJ1003-1004. All four orders PAID (manual SALE), not test, not cancelled.
