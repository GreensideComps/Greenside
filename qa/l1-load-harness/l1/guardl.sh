#!/usr/bin/env bash
# H7 L1 load-window guard loop (Stage 4 only; NOT run in Stage 1).   guardl.sh STATE_DIR
# STATE_DIR/guardl-config.json: {competition_id, pre_max_order_number, repo, restore_cmd, verify_cmd}
#   restore_cmd  the reviewed strict restore (redeploy the QA Worker with DRY_RUN=true, --env qa)
#   verify_cmd   prints exactly "true" when the QA Worker serves DRY_RUN=true on the single restored version
# Every second: collect (read-only) -> tick (decisions). Exit 10 from tick = restore requested; ANY other non-zero tick exit is
# treated the same (fail-safe). Restore = run restore_cmd, then verify_cmd;
# a failed restore is retried every 10 s and writes STATE_DIR/RESTORE-ALARM; it is never abandoned. The loop ends only when the
# restore is verified. Writes STATE_DIR/guard.pid (fire.py's live gate requires a running guard).
set -u
D="${1:?state dir}"; H="$(cd "$(dirname "$0")" && pwd)"
CFG="$D/guardl-config.json"; [ -f "$CFG" ] || { echo "REFUSED: $CFG missing"; exit 2; }
RESTORE=$(python3 -c "import json;print(json.load(open('$CFG'))['restore_cmd'])"); VERIFY=$(python3 -c "import json;print(json.load(open('$CFG'))['verify_cmd'])")
echo $$ > "$D/guard.pid"; LAST_RESTORE=0
log() { echo "$(date -u +%T) $*" >> "$D/guardl.log"; }
log "guardl start (pid $$)"
while :; do
  python3 "$H/guardl.py" collect --state-dir "$D" >> "$D/guardl-collect.log" 2>&1 || log "collect failed (inputs missing this tick: the guard treats stale inputs as LOAD STOP)"
  python3 "$H/guardl.py" tick --state-dir "$D" >> "$D/guardl-tick.log" 2>&1; rc=$?
  if [ $rc -ne 0 ] && [ $rc -ne 10 ]; then
    log "tick failed rc=$rc: fail-safe -> restore"; echo "$(date -u +%FT%TZ) guardl tick failed rc=$rc" >> "$D/safety-stop"
  fi
  if [ $rc -ne 0 ]; then
    now=$(date +%s)
    if [ $((now - LAST_RESTORE)) -ge 10 ]; then
      LAST_RESTORE=$now; log "RESTORE requested: $(python3 -c "import json;print(json.load(open('$D/guardl.json')).get('restore_reason'))" 2>/dev/null || echo 'guard state unreadable')"
      if bash -c "$RESTORE" >> "$D/guardl-restore.log" 2>&1 && [ "$(bash -c "$VERIFY" 2>/dev/null)" = "true" ]; then
        log "restore VERIFIED (DRY_RUN=true)"; python3 - "$D" <<'PY'
import json, sys, os
p = os.path.join(sys.argv[1], "guardl-input.json"); d = json.load(open(p)) if os.path.exists(p) else {}
d.setdefault("worker", {})["restored_verified"] = True; json.dump(d, open(p, "w"))
PY
        python3 "$H/guardl.py" tick --state-dir "$D" >> "$D/guardl-tick.log" 2>&1
        log "guardl done: $(python3 -c "import json;g=json.load(open('$D/guardl.json'));print(g['verdict'], g['restore_reason'])")"; exit 0
      else
        date -u +%FT%TZ >> "$D/RESTORE-ALARM"; log "RESTORE FAILED or not verified; retrying in 10 s (RESTORE-ALARM written)"
      fi
    fi
  fi
  sleep 1
done
