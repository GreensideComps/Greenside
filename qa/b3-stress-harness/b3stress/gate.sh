#!/usr/bin/env bash
# SW4 Phase B live gate: deploy DRY_RUN=false, fresh confirmation tail, stricter all-request gate (180s budget); on failure restore strictly.
S=__SCRATCHPAD__; D=$S/b3stress; cd $S/b3qa/greenside-entry-allocator && export WRANGLER_SEND_METRICS=false; U=https://greenside-entry-allocator-qa.hidden-cherry-619e.workers.dev; Q="python3 $D/tailq.py"; ts() { date -u +%T; }; nowms() { date -u +%s%3N; }; rm -f $D/gate.txt
[ "$(git rev-parse HEAD)" = e917bb504a07bf19543555cd037281e1b9e47683 ] && [ -z "$(git status --porcelain --untracked-files=all)" ] || { echo "ABORT: repo not clean at e917bb5"; exit 1; }
nowms > $D/t0.txt; echo "=== [1] deploy --env qa --var DRY_RUN:false ($(ts)) ==="; npx wrangler deploy --env qa --var DRY_RUN:false > $D/live.log 2>&1; echo "    exit=$?"; grep -E 'DRY_RUN' $D/live.log | sed 's/^/    /'
NEW=$(grep -oE 'Current Version ID: [0-9a-f-]+' $D/live.log | awk '{print $4}'); echo "    new version: $NEW"; echo "$NEW" > $D/newver.txt; BSTART=$(date +%s)
echo "=== [2] fresh confirmation tail; must capture a request ($(ts)) ==="; touch $D/conf-wanted; for i in $(seq 1 15); do sleep 1; [ -f $D/conf.pid ] && break; done; echo "    conf pid=$(cat $D/conf.pid 2>/dev/null)  pre alive=$(kill -0 $(cat $D/pre.pid) 2>/dev/null && echo yes || echo no)"
W=0; while [ $(( $(date +%s)-BSTART )) -lt 180 ]; do tag="warm-$RANDOM"; curl -sS -o /dev/null --max-time 5 "$U/health?probe=$tag"; for k in 1 2 3 4 5 6 7 8; do sleep 1; v=$($Q probe $D/conf.jsonl $tag); [ -n "$v" ] && { echo "    confirmation tail captured $tag (served by ${v:0:8}) at $(ts)"; W=1; break 2; }; done; done
GATE=0; if [ -n "$NEW" ] && [ $W = 1 ]; then touch $D/gate-active; echo "=== [3] STRICT gate: 5 x dry_run=false over >=20s, tail-confirmed on ${NEW:0:8}, zero other-version requests seen by any tail (budget 180s from deploy) ==="
  [ "$($D/strictgate.sh "$NEW" false 180 $BSTART gate | tee $D/livegate.log | tail -1)" = PASSED ] && GATE=1; grep -v -E '^(PASSED|FAILED)$' $D/livegate.log; fi
if [ $GATE -ne 1 ]; then rm -f $D/gate-active; echo "GATE_FAILED ($(ts)): C1 not started; restoring"; echo GATE_FAILED > $D/gate.txt; $D/restore.sh; exit 0; fi
echo PASSED > $D/gate.txt; nowms > $D/tgate.txt; echo "    GATE PASSED ($(ts), $(( $(date +%s)-BSTART ))s after deploy). Worker LIVE; tails + watchdog + heartbeat stay on. Waiting for the next automatic sweep."
