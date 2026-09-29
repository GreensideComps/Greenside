#!/usr/bin/env bash
# Tail supervisor: keeps TWO redundant pre-deploy tails (pre, pre2; started 20s apart, each restarted independently, so a stream
# closing never leaves a blind spot) and (when wanted) the confirmation tail running.
D="$1"; W="$2"; cd "$W" || exit 1; export WRANGLER_SEND_METRICS=false
log() { echo "$(date -u +%T) $*" >> "$D/supervisor.log"; }
start() { local s=$1; setsid bash -c "exec node_modules/.bin/wrangler tail --env qa --format json >> '$D/$s.jsonl' 2>> '$D/$s.err'" & echo $! > "$D/$s.pid"; date -u +%s > "$D/$s.started"; log "start $s pid $(cat $D/$s.pid)"; }
alive() { [ -f "$D/$1.pid" ] && kill -0 "$(cat $D/$1.pid)" 2>/dev/null; }
killslot() { [ -f "$D/$1.pid" ] && kill -TERM -- "-$(cat $D/$1.pid)" 2>/dev/null; rm -f "$D/$1.pid"; }
bump() { echo $(( $(cat "$D/$1.restarts" 2>/dev/null || echo 0) + 1 )) > "$D/$1.restarts"; log "RESTART $1 ($2) -> restarts=$(cat $D/$1.restarts)"; }
echo 0 > "$D/pre.restarts"; echo 0 > "$D/pre2.restarts"; echo 0 > "$D/conf.restarts"; start pre; sleep 20; start pre2
while [ ! -f "$D/stop" ]; do
  alive pre || { bump pre "process exited"; start pre; }
  alive pre2 || { bump pre2 "process exited"; start pre2; }
  if [ -f "$D/conf-wanted" ]; then
    if [ ! -f "$D/conf.pid" ] && [ ! -f "$D/conf.started" ]; then start conf
    elif ! alive conf; then bump conf "process exited"; start conf
    elif [ -f "$D/gate-active" ]; then
      now=$(date +%s); mt=$(stat -c %Y "$D/conf.jsonl" 2>/dev/null || echo 0); st=$(cat "$D/conf.started")
      last=$(( mt > st ? mt : st ))
      if [ $(( now - last )) -gt 10 ]; then bump conf "no events >10s"; killslot conf; start conf; fi
    fi
  elif [ -f "$D/conf.pid" ]; then killslot conf; rm -f "$D/conf.started"; log "conf stopped (not wanted)"; fi
  sleep 1
done
killslot pre; killslot pre2; killslot conf; log "supervisor stopped"
