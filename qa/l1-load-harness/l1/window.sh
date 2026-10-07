#!/usr/bin/env bash
# H12 window.sh: the L1 Stage 4 runbook (Stage 4 only, separately approved; NOT run in Stage 1). It SEQUENCES existing mechanisms
# and adds none of its own to the Worker: the B3 tails (supervisor.sh), heartbeat.sh, the B3 pre-live checkers (prestrict, sweeps,
# evidence, hooks), gate.sh (deploy --env qa --var DRY_RUN:false + strict gate), restore.sh (deploy --env qa + restore gate),
# dsnap.sh and postcheck.sh, all unchanged, with the L1 pieces: livewin.py (read-only checks, pollers, state files), sampler.py,
# guardl.sh and load.py. ONE state directory: D=$S/b3stress (the installed B3 directory), so gate.sh / restore.sh / the tails
# write newver.txt, gate.txt, t0.txt, restorever.txt, pre.jsonl, pre2.jsonl, conf.jsonl exactly where load.py and guardl read them.
#
#   window.sh install S                        offline: the EXISTING qa/b3-stress-harness/install.sh into S (it resolves __SCRATCHPAD__
#                                              to S in every B3 script), refused over a spent L1 state directory; then the
#                                              install check. The allocator worktree at e917bb5 + npm ci stay the B3 README steps.
#   window.sh prelive S PLAN PLAN_SHA          read-only: install, Worker (DRY_RUN true), monitors, state files, slot, sweeps,
#                                              continuity, no webhooks, D1 = reference, drafts OPEN. Nothing is deployed.
#   window.sh live S PLAN PLAN_SHA PHRASE      PHRASE = LOAD-QAL-750-ORDERS-1650-ENTRIES. prelive again, then: Workers Logs
#                                              poller, gate.sh, guardl-config.json, Worker poller, sampler, guardl.sh, guard
#                                              clear, pre-GO check, arm (qag-start), state check, load.py live, wait for the
#                                              guard's verified restore, dsnap.sh d1-final, postcheck.sh, final failwatch.
#   window.sh restore S                        the restore_cmd for guardl.sh and load.py: B3 restore.sh under a lock; a no-op
#                                              when the restore is already verified (so two callers never deploy twice).
# Any failure before the gate: abort, nothing deployed. Gate failure: gate.sh restores by itself (the Workers Logs poller is
# stopped). Any failure after the gate: manual-stop, so the running guard restores (SAFETY) and nothing is armed or sent; with no
# running guard the restore is run directly. Every path after the gate ends with the verified restore, dsnap.sh d1-final,
# postcheck.sh and the final failwatch.
set -u
CMD="${1:?command}"; S="${2:?scratchpad}"; D="$S/b3stress"; A="$S/b3qa/greenside-entry-allocator"; H="$(cd "$(dirname "$0")" && pwd)"
LW="python3 $H/livewin.py"; PHRASE_LIVE="LOAD-QAL-750-ORDERS-1650-ENTRIES"
ts() { date -u +%T; }; nowms() { date -u +%s%3N; }
log() { echo "$(ts) $*" | tee -a "$D/window.log"; }
abort() { log "ABORT ($1): $2"; exit "${3:-2}"; }
finish() {   # after the gate, on every path: the verified restore, then the B3 post-check and the final evidence
  if [ -f "$D/guard.pid" ] && kill -0 "$(cat "$D/guard.pid")" 2>/dev/null; then
    while kill -0 "$(cat "$D/guard.pid")" 2>/dev/null; do
      sleep 5; [ -f "$D/RESTORE-ALARM" ] && log "RESTORE-ALARM present: the restore is failing and being retried (guardl.log)"
    done
    log "guard finished: $(tail -1 "$D/guardl.log" 2>/dev/null)"
  else
    log "no running guard: strict restore directly"; bash "$H/window.sh" restore "$S"
  fi
  [ "$($LW verify-restore --state-dir "$D")" = true ] && log "restore VERIFIED (DRY_RUN=true)" || \
    log "RESTORE NOT VERIFIED: the QA Worker may still be live; run window.sh restore S now"
  bash "$D/dsnap.sh" "$D/d1-final.json" || log "d1-final snapshot failed (postcheck will fail)"
  bash "$D/postcheck.sh" 2>&1 | tee "$D/postcheck-run.log"
  $LW final --state-dir "$D"
  $LW check-state --state-dir "$D" --plan "$PLAN" --plan-sha "$SHA" --phase final
}
stop_after_gate() {
  log "ABORT after the gate ($1): $2 -> manual-stop (the guard restores DRY_RUN=true)"; touch "$D/manual-stop"; finish; exit 3
}

if [ "$CMD" = install ]; then
  for m in "$D"/load-*.done "$D"/qag-start "$D"/guardl-config.json; do
    [ -e "$m" ] && { echo "REFUSED: $D is a used L1 state directory ($(basename "$m")); install into a fresh scratchpad"; exit 2; }
  done
  bash "$H/../../b3-stress-harness/install.sh" "$S" || exit 2
  $LW install-check --scratchpad "$S" && exit 0
  echo "installed; remaining before prelive: the problems listed above (B3 README: git worktree add --detach $S/b3qa e917bb5; npm ci)"
  exit 2
fi

if [ "$CMD" = restore ]; then
  exec 9>>"$D/restore.lock"; flock -w 1800 9 || { echo "restore lock not acquired"; exit 1; }
  if [ "$($LW verify-restore --state-dir "$D")" = true ]; then echo "restore already verified (no second deploy)"; exit 0; fi
  bash "$D/restore.sh"; exit $?
fi

PLAN="${3:?bound plan}"; SHA="${4:?plan sha256}"
[ "$CMD" = prelive ] || [ "$CMD" = live ] || { echo "unknown command $CMD"; exit 2; }
if [ "$CMD" = live ] && [ "${5:-}" != "$PHRASE_LIVE" ]; then echo "REFUSED: confirmation phrase missing or wrong"; exit 2; fi
[ -d "$D" ] || { echo "REFUSED: $D missing (install the B3 harness first)"; exit 2; }

prelive() {
  log "=== pre-live ($CMD) ==="
  $LW install-check --scratchpad "$S" || abort install "B3 harness / allocator worktree not ready"
  $LW worker-before --state-dir "$D" || abort worker "QA Worker is not DRY_RUN=true on one version, or production Worker present"
  if ! pgrep -f "$D/supervisor.sh" >/dev/null; then
    nowms > "$D/mon-start-ms.txt"
    setsid nohup "$D/supervisor.sh" "$D" "$A" >/dev/null 2>&1 < /dev/null &
    setsid nohup "$D/heartbeat.sh" "$D" >/dev/null 2>&1 < /dev/null &
    sleep 12; log "monitors started (tails pre, pre2; heartbeat)"
  fi
  MS=$(cat "$D/mon-start-ms.txt")
  $LW prep --state-dir "$D" --plan "$PLAN" --plan-sha "$SHA" || abort prep "expected-subs / pre-max-order / d1-ref / drafts-open"
  for i in $(seq 1 6); do
    $LW slot --wait || abort slot "slot wait failed"
    $LW prelive-sweeps --state-dir "$D" --since-ms "$MS" --dry-version "$(cat "$D/dry-version.txt")"; rc=$?
    [ $rc -eq 3 ] || break
    log "required sweeps not captured yet; next slot"; sleep 300
  done
  [ $rc -eq 0 ] || abort sweeps "strict pre-live sweep rule or tail continuity not met (rc=$rc, prelive-sweeps.json)"
  $LW slot || abort slot "outside the hh:01-04 / hh:31-34 slot after the checks"
  $LW no-webhooks --state-dir "$D" --since-ms "$MS" || abort webhooks "webhook seen before deploy"
  $LW d1-same --state-dir "$D" || abort d1 "QA D1 changed since the reference"
  $LW drafts-open --state-dir "$D" --plan "$PLAN" --plan-sha "$SHA" || abort drafts "not all 750 plan drafts OPEN"
  $LW check-state --state-dir "$D" --plan "$PLAN" --plan-sha "$SHA" --phase prelive || abort state "pre-live state files"
  log "PRE-LIVE CHECKS PASSED"
}

prelive
if [ "$CMD" = prelive ]; then log "prelive only: nothing deployed; QA Worker stays DRY_RUN=true"; exit 0; fi

log "=== live window ==="
WLF=$(nowms); echo "$WLF" > "$D/wl-from-ms.txt"
setsid nohup $LW poll-wl --state-dir "$D" --from-ms "$WLF" --until-file "$D/stop" >> "$D/poll-wl.log" 2>&1 < /dev/null &
echo $! > "$D/poll-wl.pid"
bash "$D/gate.sh" 2>&1 | tee "$D/gate-run.log"
if [ "$(cat "$D/gate.txt" 2>/dev/null)" != PASSED ]; then
  kill "$(cat "$D/poll-wl.pid")" 2>/dev/null; abort gate "gate not PASSED; gate.sh has restored the Worker; nothing armed" 2
fi
log "gate PASSED: QA Worker live on $(cat "$D/newver.txt")"
RESTORE_CMD="bash $H/window.sh restore $S"; VERIFY_CMD="python3 $H/livewin.py verify-restore --state-dir $D"
$LW config --state-dir "$D" --plan "$PLAN" --plan-sha "$SHA" --wl-from-ms "$WLF" --restore-cmd "$RESTORE_CMD" \
  --verify-cmd "$VERIFY_CMD" || stop_after_gate config "guardl-config.json not written"
setsid nohup $LW poll-worker --state-dir "$D" --until-file "$D/stop" >> "$D/poll-worker.log" 2>&1 < /dev/null &
echo $! > "$D/poll-worker.pid"
setsid nohup python3 "$H/sampler.py" run --state-dir "$D" --expect-subs "$D/expected-subs.json" --until-file "$D/stop" \
  >> "$D/sampler.log" 2>&1 < /dev/null &
echo $! > "$D/sampler.pid"
setsid nohup bash "$H/guardl.sh" "$D" >> "$D/guardl-run.log" 2>&1 < /dev/null &
ok=0
for i in $(seq 1 75); do
  sleep 2
  if [ -f "$D/guard.pid" ] && ! kill -0 "$(cat "$D/guard.pid")" 2>/dev/null; then stop_after_gate guard "guardl.sh exited before arming"; fi
  $LW check-state --state-dir "$D" --plan "$PLAN" --plan-sha "$SHA" --phase guarded > /dev/null && { ok=1; break; }
done
[ $ok = 1 ] || stop_after_gate guard "guard, Worker reading, sampler or Workers Logs not clear within 150 s"
$LW no-webhooks --state-dir "$D" --since-ms "$(cat "$D/mon-start-ms.txt")" || stop_after_gate pre-go "webhook seen before GO"
$LW d1-same --state-dir "$D" || stop_after_gate pre-go "QA D1 changed before GO"
nowms > "$D/qag-start"
$LW check-state --state-dir "$D" --plan "$PLAN" --plan-sha "$SHA" --phase armed || stop_after_gate armed "armed state check"
log "ARMED at $(ts) UTC: load.py live"
python3 "$H/load.py" live --plan "$PLAN" --plan-sha "$SHA" --state-dir "$D" --confirm "$PHRASE_LIVE" \
  --restore-cmd "$RESTORE_CMD" 2>&1 | tee "$D/load-run.log"; LRC=${PIPESTATUS[0]}
log "load.py exit $LRC"
[ "$LRC" = 2 ] && { log "load.py refused before any mutation -> manual-stop"; touch "$D/manual-stop"; }
finish
log "WINDOW DONE: load.py $LRC, postcheck $(cat "$D/postcheck.txt" 2>/dev/null). Offline next: shopsnapl.py, loadrecon.py, loadmetrics.py, manifest.py"
[ "$(cat "$D/postcheck.txt" 2>/dev/null)" = PASSED ] && [ "$LRC" = 0 ] || exit 3
exit 0
