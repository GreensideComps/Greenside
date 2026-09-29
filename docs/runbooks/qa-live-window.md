# Runbook — QA live window (DRY_RUN=false on the QA Worker)

Applies ONLY to `greenside-entry-allocator-qa`. Every step needs explicit approval of the test it
belongs to. Scripts: Repo — `/qa/b3-stress-harness/b3stress/` (install per
`/qa/b3-stress-harness/README.md`).

## Preconditions

- Allocator worktree at the approved commit, clean. QA Worker on the approved version with
  `DRY_RUN=true`.
- Fresh monitoring window: both tails and the heartbeat running.
- Reference snapshots of Shopify and D1 taken and matching the expected state.

## Sequence

1. **Pre-live checks** at the slot (hh:01–04 or hh:31–34):
   - the latest trailing and deep dry-run sweeps are all zeros
   - tail continuity is proven (`evidence.py`)
   - no webhooks
   - Shopify and D1 unchanged
2. **Deploy** with `--env qa --var DRY_RUN:false` (`gate.sh`).
3. **Strict gate**: five consecutive `dry_run=false` probes over at least 20 seconds, each
   confirmed by a tail on the new version, and no requests seen on any other version. On
   failure: restore.
4. **Guard** (`guard.sh`) runs until the planned restore. It checks D1 and Shopify
   allow-lists, webhooks and sweeps, heartbeat freshness, evidence continuity and the
   deadlines. Any deviation triggers a strict restore.
5. **Planned end**: the pool is complete, the lifecycle is complete, and at least one
   qualifying live zero sweep has run.
6. **Restore** (`restore.sh`): redeploy with `DRY_RUN=true`, then run the restore gate.
7. **Post-check** (`postcheck.sh`): the first dry-run sweep after the restore is clean, D1
   matches the last live snapshot, and integrity counters are 0.

## Deadlines (guard)

- No order within 1200 seconds of arming.
- Fewer than all orders within 120 seconds of the first order.
- Pool incomplete 300 seconds after the first order.
- Create+paid lifecycle incomplete 300 seconds after the first order.
- Shopify incomplete 300 seconds after the pool.
- No qualifying zero sweep within 1500 seconds of completion.

## If the session or container is lost while the Worker is live

Check the Worker's `DRY_RUN` value and whether the guard is still running. If the Worker is
live and unguarded: run `restore.sh`, then `postcheck.sh`, then report. Schedule a fail-safe
check-in before every live window.
