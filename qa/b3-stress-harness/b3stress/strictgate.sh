#!/usr/bin/env bash
# Stricter all-request gate (SW1B). Usage: strictgate.sh TARGET_VERSION EXPECT(true|false) BUDGET_S BSTART_EPOCH LABEL
# Streak = consecutive /health probes returning EXPECT, each tail-confirmed on TARGET; the streak resets if ANY tail
# (pre or conf) observes ANY request served by a version other than TARGET from 2s before the streak began onward,
# or if the confirmation tail restarts. Needs >=5 probes spanning >=20s and a clean re-check after an 8s tail flush.
D=__SCRATCHPAD__/b3stress; U=https://greenside-entry-allocator-qa.hidden-cherry-619e.workers.dev
TGT=$1; EXP=$2; BUDGET=$3; BSTART=$4; L=$5; Q="python3 $D/tailq.py"; ts() { date -u +%T; }; nowms() { date -u +%s%3N; }
old() { python3 $D/oldreq.py "$TGT" "$1" $D/pre.jsonl $D/pre2.jsonl $D/conf.jsonl 2>>$D/$L-old.log; }
within() { [ $(( $(date +%s) - BSTART )) -lt $BUDGET ]; }
STREAK=0; SSTART=0; n=0; R0=$(cat $D/conf.restarts)
while within; do
  if [ "$(cat $D/conf.restarts)" != "$R0" ]; then R0=$(cat $D/conf.restarts); STREAK=0; echo "    confirmation tail restarted: streak reset"; continue; fi
  n=$((n+1)); tag="$L-$n-$RANDOM"; T=$(nowms); got=$(curl -sS --max-time 5 "$U/health?probe=$tag" | jq -r .dry_run 2>/dev/null); v=""
  for k in $(seq 1 10); do sleep 1; v=$($Q probe $D/conf.jsonl $tag); [ -n "$v" ] && break; done
  if [ -z "$v" ]; then STREAK=0; note="not seen in tail: reset"
  elif [ "$v" != "$TGT" ]; then STREAK=0; note="served by ${v:0:8} (not target): reset"
  elif [ "$got" != "$EXP" ]; then STREAK=0; note="dry_run=$got: reset"
  else [ $STREAK -eq 0 ] && SSTART=$T; STREAK=$((STREAK+1)); note="tail-confirmed ${v:0:8}"; fi
  if [ $STREAK -gt 0 ]; then o=$(old $((SSTART-2000))); [ "$o" -gt 0 ] && { STREAK=0; note="$note; tail saw $o old-version request(s) during streak: reset"; }; fi
  echo "    $tag $(ts): dry_run=$got served_by=${v:0:8} streak=$STREAK ($note)"
  if [ $STREAK -ge 5 ] && [ $(( T - SSTART )) -ge 20000 ]; then
    echo "    5 consecutive over $(( (T-SSTART)/1000 ))s; 8s tail flush, then all-request re-check..."; sleep 8
    o=$(old $((SSTART-2000)))
    if [ "$(cat $D/conf.restarts)" != "$R0" ]; then echo "    tail restarted during flush: reset"; STREAK=0
    elif [ "$o" -gt 0 ]; then echo "    tail saw $o old-version request(s) since streak start: reset"; STREAK=0
    else echo "    CLEAN: no request on any other version seen by either tail since $(date -u -d @$(( (SSTART-2000)/1000 )) +%T)"; echo PASSED; exit 0; fi
  fi
  sleep 3
done
echo "    budget ${BUDGET}s exhausted"; echo FAILED; exit 1
