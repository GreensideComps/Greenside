#!/usr/bin/env bash
# B3 STRESS-window guard. Runs from gate PASSED until the planned restore. Every 3s: cstate.py (webhooks + sweeps),
# heartbeat must be dry_run=false and fresh, no tail restart from arming until completion, test deadlines.
# Every 15s (and at once on a new webhook/sweep): D1 allow-list (dstate.py) and Shopify allow-list (shopstate.py); orders are bound
# only from Shopify (tagged stress orders with exactly one stress line), and every D1 allocation's order must be one of them.
# Any deviation, or manual-stop file -> strict restore at once.
# Complete when: D1 pool complete + Shopify complete (all orders, stock 0/0) + create AND paid seen for every order + >=1 qualifying
# live */15 sweep after completion (all zeros) -> concurrency evidence recorded -> planned restore.
D=__SCRATCHPAD__/b3stress; TG=$(cat $D/tgate.txt); NEW=$(cat $D/newver.txt)
NORD=$(jq '.order_quantities|length' $D/stress.json)
log() { echo "$(date -u +%T) $*" >> $D/guard.log; }; R0="$(cat $D/pre.restarts)/$(cat $D/pre2.restarts)/$(cat $D/conf.restarts)"; WHY=""; ARMED=""; FIRST=""; DONE=""; LASTX=0; LASTSIG=""
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
  now=$(date +%s); R="$(cat $D/pre.restarts)/$(cat $D/pre2.restarts)/$(cat $D/conf.restarts)"
  [ -z "$ARMED" ] && [ -f $D/qag-start ] && { ARMED=$(( $(cat $D/qag-start)/1000 )); log "armed at $(date -u -d @$ARMED +%T)"; }
  SIG="$(jq -c '[(.hooks|length),(.sweeps|length)]' $D/cstate.json)"
  if [ "$SIG" != "$LASTSIG" ] || [ $((now - LASTX)) -ge 15 ]; then xcheck || break; LASTSIG=$SIG; now=$(date +%s); fi
  hd=$(jq -r .hooks_done $D/cstate.json); z=$(jq -r .zero_sweeps_after_complete $D/cstate.json); n=$(jq '.sweeps|length' $D/cstate.json); nh=$(jq '.hooks|length' $D/cstate.json)
  [ "$nh" -gt "${LASTH:-0}" ] && { for i in $(seq ${LASTH:-0} $((nh-1))); do log "webhook $((i+1)): $(jq -c ".hooks[$i]|{topic,order,webhook_id,triggered_at,ts,wall,status,version,hmac_header,lines}" $D/cstate.json)"; done; LASTH=$nh; }
  inburst=0; [ -n "$ARMED" ] && [ ! -f $D/planned-restore ] && inburst=1
  [ -n "$ARMED" ] && [ -z "$FIRST" ] && [ $now -gt $((ARMED+1200)) ] && { WHY="NO STRESS ORDER 1200s after arming"; break; }
  [ -n "$FIRST" ] && [ "$(wc -l < $D/bound-orders.txt)" -lt "$NORD" ] && [ $now -gt $((FIRST+120)) ] && { WHY="ONLY $(wc -l < $D/bound-orders.txt) OF $NORD ORDERS 120s after the first"; break; }
  [ -n "$FIRST" ] && [ -z "$DONE" ] && [ $now -gt $((FIRST+300)) ] && { WHY="POOL NOT COMPLETE 300s after the first order: $(jq -c '{allocated,orders}' $D/dstate.json)"; break; }
  [ -n "$FIRST" ] && [ "$hd" != true ] && [ $now -gt $((FIRST+300)) ] && { WHY="create+paid NOT SEEN FOR EVERY ORDER 300s after the first: $(jq -c .per_topic $D/cstate.json)"; break; }
  [ -n "$DONE" ] && [ "$(jq -r .complete $D/shopstate.json)" != true ] && [ $now -gt $((DONE+300)) ] && { WHY="SHOPIFY NOT COMPLETE 300s after the pool: $(jq -c '{stock,stock_expected}' $D/shopstate.json)"; break; }
  [ -n "$DONE" ] && [ "$z" -lt 1 ] && [ $now -gt $((DONE+1500)) ] && { WHY="NO QUALIFYING LIVE */15 SWEEP 1500s after the pool completed"; break; }
  if [ "$R" != "$R0" ]; then if [ $inburst = 1 ]; then WHY="TAIL RESTART DURING TEST ($R0 -> $R)"; break; else log "tail restart outside test ($R0 -> $R)"; R0=$R; fi; fi
  [ "$n" -gt "${LASTN:-0}" ] && { log "sweep $n OK: $(jq -c ".sweeps[$((n-1))]|{run_id,cron,mode,after_complete,summary,converged}" $D/cstate.json)"; LASTN=$n; }
  if [ -n "$DONE" ] && [ "$hd" = true ] && [ "$z" -ge 1 ] && [ ! -f $D/planned-restore ]; then
    xcheck || break
    if [ "$(jq -r .complete $D/shopstate.json)" = true ] && [ "$(jq -r .complete $D/dstate.json)" = true ]; then
      python3 $D/overlap.py $D $(( ARMED*1000 )) $(( $(date +%s)*1000 )) > $D/overlap.json
      log "CONCURRENCY: $(jq -c '{verdict,counts,peak_in_flight}' $D/overlap.json)"
      log "stress test complete: pool complete, Shopify complete $(jq -c '{stock,stock_expected}' $D/shopstate.json), per-topic $(jq -c .per_topic $D/cstate.json), $z qualifying zero sweep(s) -> planned restore"; touch $D/planned-restore
    else log "not yet converged (D1 $(jq -r .complete $D/dstate.json), Shopify $(jq -c '{stock,stock_expected}' $D/shopstate.json)); re-checking"; fi; fi
  if [ -f $D/planned-restore ]; then log "stress window done -> planned strict restore"; echo PLANNED > $D/guard-result.txt; $D/restore.sh >> $D/guard.log 2>&1; log "restoregate.txt: $(cat $D/restoregate.txt)"
    $D/postcheck.sh >> $D/guard.log 2>&1; log "postcheck.txt: $(cat $D/postcheck.txt)"; exit 0; fi
  sleep 3
done
log "STOP: $WHY -> strict restore"; echo "STOP: $WHY" > $D/guard-result.txt; $D/restore.sh >> $D/guard.log 2>&1; log "restoregate.txt: $(cat $D/restoregate.txt)"
python3 $D/overlap.py $D $(( ${ARMED:-0}*1000 )) $(( $(date +%s)*1000 )) > $D/overlap.json 2>/dev/null; $D/postcheck.sh >> $D/guard.log 2>&1; log "postcheck.txt: $(cat $D/postcheck.txt)"; exit 2
