#!/usr/bin/env bash
# B3 STRESS-window guard. Runs from gate PASSED until the planned restore. Every 3s: cstate.py (webhooks + sweeps),
# heartbeat must be dry_run=false and fresh, test deadlines, and NO LOSS OF EVIDENCE (evidence.py over [gate PASSED, now - 15s]:
# every heartbeat probe seen by at least one redundant tail, gaps within the existing 12s limit, the two tails never unavailable
# at the same time; and at least one of the two tail processes alive). A tail gap may be recovered ONLY by evidence.py's strict Workers
# Logs rules (b3obs/obsq.py; 60s hard wait, complete query, every gap probe present, agreement); PENDING never counts as intact. A tail restart alone is logged, not a stop: restarts are
# classified by their own timestamp (before qag-start = outside the test) and read only after the cross-check (no race).
# Every 15s (and at once on a new webhook/sweep): D1 allow-list (dstate.py) and Shopify allow-list (shopstate.py); orders are bound
# only from Shopify (tagged stress orders with exactly one stress line), and every D1 allocation's order must be one of them.
# Any deviation, or manual-stop file -> strict restore at once.
# Complete when: D1 pool complete + Shopify complete (all orders, stock 0/0) + create AND paid seen for every order + >=1 qualifying
# live */15 sweep after completion (all zeros) -> held until the evidence check has covered the completion moment -> concurrency
# evidence recorded -> planned restore.
D=__SCRATCHPAD__/b3stress; TG=$(cat $D/tgate.txt); NEW=$(cat $D/newver.txt)
NORD=$(jq '.order_quantities|length' $D/stress.json)
log() { echo "$(date -u +%T) $*" >> $D/guard.log; }; R0="$(cat $D/pre.restarts)/$(cat $D/pre2.restarts)/$(cat $D/conf.restarts)"; WHY=""; ARMED=""; FIRST=""; DONE=""; LASTX=0; LASTSIG=""
LAG=15; SLN=$(wc -l < $D/supervisor.log); CT=""; EVOK=""   # evidence is judged up to now - LAG (tail delivery latency); SLN = supervisor.log lines already seen
alive() { [ -f $D/$1.pid ] && kill -0 "$(cat $D/$1.pid)" 2>/dev/null; }
echo $$ > $D/guard.pid; log "guard start TG=$TG NEW=${NEW:0:8} restarts=$R0 (stress, $NORD orders)"
xcheck() {  # D1 + Shopify allow-lists; D1 first so every allocation's order must already be visible in the later Shopify snapshot
  $D/dsnap.sh $D/d1-now.json || { WHY="D1 snapshot failed"; return 1; }
  python3 $D/dstate.py $D/d1-ref.json $D/d1-now.json $D/stress.json > $D/dstate.json || { WHY="D1 DEVIATION: $(jq -c .deviations $D/dstate.json 2>/dev/null)"; return 1; }
  cp $D/d1-now.json $D/d1-final.json   # the D1 check passed: last known-good live D1 state (postcheck compares against it)
  $D/shopsnap.sh $D/shop-now.json "$(jq -r .product_gid $D/stress.json)" || { WHY="Shopify snapshot failed"; return 1; }
  python3 $D/shopstate.py $D/shop-ref.json $D/shop-now.json $D/stress.json > $D/shopstate.json || { WHY="SHOPIFY DEVIATION: $(jq -c .deviations $D/shopstate.json 2>/dev/null)"; return 1; }
  jq -r '.new_orders[].id' $D/shopstate.json | sort > $D/bound-orders.new
  if [ -s $D/bound-orders.new ]; then
    [ -f $D/bound-orders.txt ] && ! comm -23 $D/bound-orders.txt $D/bound-orders.new | cmp -s - /dev/null && { WHY="a bound order disappeared from Shopify"; return 1; }
    cmp -s $D/bound-orders.new $D/bound-orders.txt 2>/dev/null || { mv $D/bound-orders.new $D/bound-orders.txt; log "bound orders: $(tr '\n' ' ' < $D/bound-orders.txt)"; [ -z "$FIRST" ] && FIRST=$(date +%s); }
  fi
  for o in $(jq -r '.orders[]' $D/dstate.json); do grep -qx "gid://shopify/Order/$o" $D/bound-orders.txt 2>/dev/null || { WHY="D1 allocation for order $o which is not a bound stress order"; return 1; }; done
  if [ -z "$DONE" ] && [ "$(jq -r .complete $D/dstate.json)" = true ]; then DONE=$(date +%s); date -u +%s%3N > $D/complete-ms; cp $D/d1-now.json $D/d1-complete.json
    log "POOL COMPLETE: $(jq -c '{allocated,allocations,new_events}' $D/dstate.json)"; fi
  LASTX=$(date +%s); return 0; }
while :; do
  [ -f $D/manual-stop ] && { WHY="MANUAL STOP: $(cat $D/manual-stop)"; break; }
  python3 $D/cstate.py $D $TG $NEW > $D/cstate.json 2>$D/cstate.err || { WHY="cstate.py failed: $(tail -2 $D/cstate.err)"; break; }
  DV=$(jq -r '.deviations|length' $D/cstate.json); [ "$DV" != 0 ] && { WHY="DEVIATION: $(jq -c .deviations $D/cstate.json)"; break; }
  L=$(tail -1 $D/heartbeat.log); echo "$L" | grep -q 'dry_run=false' || { WHY="HEARTBEAT NOT false: $L"; break; }
  [ $(( $(date +%s) - $(date -d "$(date -u +%F) ${L%% *}Z" +%s) )) -gt 20 ] && { WHY="HEARTBEAT STALE: $L"; break; }
  now=$(date +%s)
  [ -z "$ARMED" ] && [ -f $D/qag-start ] && { ARMED=$(( $(cat $D/qag-start)/1000 )); log "armed at $(date -u -d @$ARMED +%T)"; }
  SIG="$(jq -c '[(.hooks|length),(.sweeps|length)]' $D/cstate.json)"
  if [ "$SIG" != "$LASTSIG" ] || [ $((now - LASTX)) -ge 15 ]; then xcheck || break; LASTSIG=$SIG; now=$(date +%s); fi
  # Tail restarts: read AFTER the cross-check; each is classified by its own supervisor.log time against qag-start, and logged only
  R="$(cat $D/pre.restarts)/$(cat $D/pre2.restarts)/$(cat $D/conf.restarts)"
  if [ "$R" != "$R0" ]; then AMS=$(cat $D/qag-start 2>/dev/null || echo 9999999999999)
    tail -n +$((SLN+1)) $D/supervisor.log | grep ' RESTART ' | while read -r t rest; do
      [ $(date -u -d "$(date -u +%F) ${t}Z" +%s%3N) -lt $AMS ] && c="before qag-start: outside the test" || c="during the test: tolerated only while no evidence is lost"
      log "tail restart at $t ($c): $rest"; done
    SLN=$(wc -l < $D/supervisor.log); R0=$R; fi
  alive pre || alive pre2 || { WHY="BOTH TAILS DOWN: neither pre nor pre2 process is running"; break; }
  # Loss of evidence (the stop rule for tails): judged over [gate PASSED, now - LAG]
  if [ ! -f $D/planned-restore ]; then EVTO=$(( $(date +%s%3N) - LAG*1000 ))
    python3 $D/evidence.py $D $TG $EVTO $(cat $D/qag-start 2>/dev/null || echo 9999999999999) false > $D/evidence.json 2>$D/evidence.err; erc=$?
    GS=$(jq -c '[.gaps[]?|[.from,.to,.status]]' $D/evidence.json 2>/dev/null); [ "$GS" != "${LASTGS:-[]}" ] && { log "tail gaps: $(jq -c '[.gaps[]?|{from,to,open,status,notes,phase1:(.phase1|if . then {complete,records,probes_proven,merged,wl_isolated_loss} else null end),phase2:(.phase2|if . then {complete,records,merged} else null end)}]' $D/evidence.json)"; LASTGS=$GS; }
    if [ $erc = 2 ]; then WHY="EVIDENCE LOST: $(jq -c .lost $D/evidence.json)"; break
    elif [ $erc = 0 ]; then EVOK=$EVTO; elif [ $erc != 3 ] && [ $erc != 4 ]; then WHY="evidence.py failed rc=$erc: $(tail -2 $D/evidence.err)"; break; fi; fi   # 4 = gap PENDING recovery: not intact, keep checking
  hd=$(jq -r .hooks_done $D/cstate.json); z=$(jq -r .zero_sweeps_after_complete $D/cstate.json); n=$(jq '.sweeps|length' $D/cstate.json); nh=$(jq '.hooks|length' $D/cstate.json)
  [ "$nh" -gt "${LASTH:-0}" ] && { for i in $(seq ${LASTH:-0} $((nh-1))); do log "webhook $((i+1)): $(jq -c ".hooks[$i]|{topic,order,webhook_id,triggered_at,ts,wall,status,version,hmac_header,lines}" $D/cstate.json)"; done; LASTH=$nh; }
  [ -n "$ARMED" ] && [ -z "$FIRST" ] && [ $now -gt $((ARMED+1200)) ] && { WHY="NO STRESS ORDER 1200s after arming"; break; }
  [ -n "$FIRST" ] && [ "$(wc -l < $D/bound-orders.txt)" -lt "$NORD" ] && [ $now -gt $((FIRST+120)) ] && { WHY="ONLY $(wc -l < $D/bound-orders.txt) OF $NORD ORDERS 120s after the first"; break; }
  [ -n "$FIRST" ] && [ -z "$DONE" ] && [ $now -gt $((FIRST+300)) ] && { WHY="POOL NOT COMPLETE 300s after the first order: $(jq -c '{allocated,orders}' $D/dstate.json)"; break; }
  [ -n "$FIRST" ] && [ "$hd" != true ] && [ $now -gt $((FIRST+300)) ] && { WHY="create+paid NOT SEEN FOR EVERY ORDER 300s after the first: $(jq -c .per_topic $D/cstate.json)"; break; }
  [ -n "$DONE" ] && [ "$(jq -r .complete $D/shopstate.json)" != true ] && [ $now -gt $((DONE+300)) ] && { WHY="SHOPIFY NOT COMPLETE 300s after the pool: $(jq -c '{stock,stock_expected}' $D/shopstate.json)"; break; }
  [ -n "$DONE" ] && [ "$z" -lt 1 ] && [ $now -gt $((DONE+1500)) ] && { WHY="NO QUALIFYING LIVE */15 SWEEP 1500s after the pool completed"; break; }
  [ "$n" -gt "${LASTN:-0}" ] && { log "sweep $n OK: $(jq -c ".sweeps[$((n-1))]|{run_id,cron,mode,after_complete,summary,converged}" $D/cstate.json)"; LASTN=$n; }
  if [ -n "$DONE" ] && [ "$hd" = true ] && [ "$z" -ge 1 ] && [ ! -f $D/planned-restore ]; then
    xcheck || break
    if [ "$(jq -r .complete $D/shopstate.json)" = true ] && [ "$(jq -r .complete $D/dstate.json)" = true ]; then
      [ -z "$CT" ] && { CT=$(date +%s%3N); log "complete; holding the planned restore until the evidence check has covered $(date -u -d @$((CT/1000)) +%T) (about ${LAG}s)"; }
      if [ "${EVOK:-0}" -ge "$CT" ]; then
      log "EVIDENCE INTACT from gate PASSED through $(date -u -d @$((EVOK/1000)) +%T): $(jq -c '{coverage:.coverage|{probes,covered,max_gap_ms,result},restarts,both_unavailable}' $D/evidence.json)"
      python3 $D/overlap.py $D $(( ARMED*1000 )) $(( $(date +%s)*1000 )) > $D/overlap.json
      log "CONCURRENCY: $(jq -c '{verdict,counts,peak_in_flight}' $D/overlap.json)"
      log "stress test complete: pool complete, Shopify complete $(jq -c '{stock,stock_expected}' $D/shopstate.json), per-topic $(jq -c .per_topic $D/cstate.json), $z qualifying zero sweep(s) -> planned restore"; touch $D/planned-restore; fi
    else log "not yet converged (D1 $(jq -r .complete $D/dstate.json), Shopify $(jq -c '{stock,stock_expected}' $D/shopstate.json)); re-checking"; fi; fi
  if [ -f $D/planned-restore ]; then log "stress window done -> planned strict restore"; echo PLANNED > $D/guard-result.txt; $D/restore.sh >> $D/guard.log 2>&1; log "restoregate.txt: $(cat $D/restoregate.txt)"
    $D/postcheck.sh >> $D/guard.log 2>&1; log "postcheck.txt: $(cat $D/postcheck.txt)"; exit 0; fi
  sleep 3
done
log "STOP: $WHY -> strict restore"; echo "STOP: $WHY" > $D/guard-result.txt; $D/restore.sh >> $D/guard.log 2>&1; log "restoregate.txt: $(cat $D/restoregate.txt)"
python3 $D/overlap.py $D $(( ${ARMED:-0}*1000 )) $(( $(date +%s)*1000 )) > $D/overlap.json 2>/dev/null; $D/postcheck.sh >> $D/guard.log 2>&1; log "postcheck.txt: $(cat $D/postcheck.txt)"; exit 2
