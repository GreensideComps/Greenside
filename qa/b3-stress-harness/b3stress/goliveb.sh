#!/usr/bin/env bash
# B3 STRESS go-live: monitors, fresh Shopify + D1 references (after the approved setup: stress product, stress competition registered,
# draft orders created), strict pre-live checks, slot wait, strict pre-live sweeps, deploy + strict gate, guard, pre-GO re-check, arm.
# Firing the draft completions is NOT done here: it is a separate, explicitly approved step after READY FOR GO.
S=__SCRATCHPAD__; D=$S/b3stress; A=$S/b3qa/greenside-entry-allocator; ts() { date -u +%T; }
CFG=$D/stress.json; DRYVER=$(jq -r .dry_version $CFG); PG=$(jq -r .product_gid $CFG); CID=$(jq -r .competition_id $CFG); PFX=$(jq -r .prefix $CFG); CAP=$(jq -r .capacity $CFG)
B="https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/scripts/greenside-entry-allocator-qa"; H="Authorization: Bearer $CLOUDFLARE_API_TOKEN"
abort() { echo "ABORT BEFORE DEPLOY ($(ts)): $*"; exit 1; }
grep -q PLACEHOLDER $CFG && abort "stress.json still has placeholders (setup not done)"
cd $A && [ "$(git rev-parse HEAD)" = 610e1899f352c09848c3bbc79630a0a9289d5658 ] && [ -z "$(git status --porcelain --untracked-files=all)" ] || abort "repo not clean"
if ! pgrep -f "b3stress/supervisor.sh" >/dev/null; then date -u +%s%3N > $D/mon-start-ms.txt; setsid nohup $D/supervisor.sh $D $A >/dev/null 2>&1 < /dev/null & setsid nohup $D/heartbeat.sh $D >/dev/null 2>&1 < /dev/null & sleep 12; fi
echo "monitoring since $(date -u -d @$(( $(cat $D/mon-start-ms.txt)/1000 )) +%T) UTC"
shopcheck() { $D/shopsnap.sh $D/shop-$1.json $PG
  O=$(cmp -s <(jq -S .orders $D/shop-$1.json) <(jq -S .orders $D/shop-ref.json) && echo YES || echo NO); P=$(cmp -s <(jq -S '[.product_a,.product_b,.products]' $D/shop-$1.json) <(jq -S '[.product_a,.product_b,.products]' $D/shop-ref.json) && echo YES || echo NO)
  echo "shopify($1): orders identical $O, products identical $P, orders $(jq '.orders|length' $D/shop-$1.json), stock $PFX $(jq .product_b.variants.nodes[0].inventoryQuantity $D/shop-$1.json)"; [ "$O$P" = YESYES ]; }
d1check() { $D/dsnap.sh $D/d1-$1.json && cmp -s <(jq -S . $D/d1-$1.json) <(jq -S . $D/d1-ref.json) && echo "d1($1): identical to reference"; }
wcheck() { { curl -sS -H "$H" $B/deployments | jq -c '.result.deployments[0] | {created_on, versions}'; curl -sS -H "$H" $B/settings | jq -c '[.result.bindings[]|select(.name=="DRY_RUN")|.text]'; curl -sS -H "$H" $B/schedules | jq -c '[.result.schedules[].cron]'; } | tee $D/worker-$1.txt; }
wcheck before; grep -q "$DRYVER\",\"percentage\":100" $D/worker-before.txt && grep -q '\["true"\]' $D/worker-before.txt || abort "Worker not $DRYVER / DRY_RUN true"
# Fresh Shopify reference, asserted against the known QA state after setup
$D/shopsnap.sh $D/shop-ref.json $PG || abort "Shopify reference snapshot failed"
jq -e --arg q "$PG" --arg p "$PFX" --argjson cap "$CAP" '(.errors|length)==0 and (.orders|length)<96
  and .product_a.variants.nodes[0].inventoryQuantity==7
  and .product_b.id==$q and .product_b.status=="ACTIVE" and (.product_b.variants.nodes|length)==1
  and .product_b.variants.nodes[0].inventoryQuantity==$cap and .product_b.totalInventory==$cap and .product_b.variants.nodes[0].inventoryPolicy=="DENY"
  and ([.product_b.metafields.nodes[]|select(.namespace=="custom")|{(.key):.value}]|add)=={"entry_prefix":$p,"entry_start_number":"1001","entries_total":($cap|tostring),"skill_mode":"none"}
  and ([.products[]|select(.id=="gid://shopify/Product/15902004674934")|.totalInventory]==[1])
  and ([.products[]|select(.id=="gid://shopify/Product/15902724817270")|.totalInventory]==[5])
  and ([.orders[]|select(.name=="#1020")|select(.cancelledAt!=null and .displayFinancialStatus=="REFUNDED")]|length)==1
  and ([.orders[]|select(.name=="#1016" or .name=="#1017" or .name=="#1018" or .name=="#1019")|select(.cancelledAt==null and .displayFinancialStatus=="PAID")]|length)==4
  and ([.orders[]|.lineItems.nodes[]|select(.product.id==$q)]|length)==0' $D/shop-ref.json >/dev/null || abort "Shopify reference is not the expected post-setup QA state"
echo "shopify reference: $(jq -c '{orders:(.orders|length), qae:.product_a.variants.nodes[0].inventoryQuantity, stress:[.product_b.totalInventory,.product_b.variants.nodes[0].inventoryQuantity]}' $D/shop-ref.json)"
# Full D1 reference: identical to the post-registration snapshot; stress pool fresh; integrity 0
$D/dsnap.sh $D/d1-ref.json || abort "D1 reference snapshot failed"
python3 - $D/d1-ref.json "$(jq -r .d1_registration_snapshot $CFG)" $CFG <<'PY' || abort "D1 reference is not the expected post-registration QA state"
import json, sys
a, b, cfg = json.load(open(sys.argv[1])), json.load(open(sys.argv[2])), json.load(open(sys.argv[3])); P = lambda d, k: d[k] if not isinstance(d[k], str) else json.loads(d[k])
C = cfg['competition_id']; c = [x for x in P(a, 'competitions') if x['competition_id'] == C]; e = [x for x in P(a, 'entries') if x[0] == C]; i = P(a, 'integrity')
ok = a == b and len(c) == 1 and c[0]['status'] == 'OPEN' and c[0]['capacity'] == cfg['capacity'] and c[0]['prefix'] == cfg['prefix'] \
     and [x[2] + '/' + x[3] + '/' + str(x[8]) for x in e] == [f"{cfg['prefix']}{n}/AVAILABLE/0" for n in range(cfg['start_number'], cfg['start_number'] + cfg['capacity'])] \
     and not [x for x in P(a, 'allocations') if x[2] == C] and all(v == 0 for k, v in i.items() if k not in ('events_total', 'allocations_total'))
print(f"d1 reference: identical to post-registration {a == b}, stress {c[0]['status'] if c else None} pool {[x[2] + '/' + x[3] for x in e]}, integrity {i}"); sys.exit(0 if ok else 2)
PY
hooks() { cat $D/pre.jsonl $D/pre2.jsonl $D/conf.jsonl $D/obs.jsonl 2>/dev/null | python3 $D/hooks.py /dev/stdin all $(cat $D/mon-start-ms.txt) | wc -l; }
MS=$(( $(cat $D/mon-start-ms.txt)/1000 )); now=$(date +%s); base=$(( now - now%3600 - 3600 ))
for c in $(seq 0 12); do S0=$(( base + c*1800 + 60 )); DEEP=$(( S0 - 660 )); [ $(( S0 + 180 )) -ge $now ] && [ $DEEP -gt $MS ] && break; done
[ $S0 -lt $now ] && S0=$now; echo "slot: $(date -u -d @$S0 +%T) UTC ($(date -u -d @$((S0+3600)) +%H:%M:%S) BST); preceding deep sweep captured after monitor start"
T15=$(( S0 - S0%900 + 900 )); echo "planned fire: $(date -u -d @$((T15-10)) +%T) UTC, 10s before the live */15 sweep at $(date -u -d @$T15 +%T) UTC" | tee $D/fire-plan.txt
until [ $(date +%s) -ge $S0 ]; do sleep 2; [ "$(hooks)" -gt 0 ] && abort "webhook seen before deploy"; done
python3 $D/prestrict.py $D $(cat $D/mon-start-ms.txt) $DRYVER > $D/prestrict.json; rc=$?; cat $D/prestrict.json; echo; [ $rc -eq 0 ] || abort "strict pre-live sweep rule not met (rc=$rc)"
# Continuity: the redundant tails must have covered every heartbeat probe from 60s before the earlier required sweep until now - 15s
# (evidence.py; a tail gap may only be recovered from Workers Logs by its strict rules; PENDING is waited for, never accepted)
SW0=$(python3 $D/sweeps.py $D $(cat $D/mon-start-ms.txt) | python3 -c "import sys,json;p=json.load(open('$D/prestrict.json'));ids={s['run_id'] for s in p['sweeps']};print(min(json.loads(l)['ts'] for l in sys.stdin if any(isinstance(m,dict) and m.get('run_id') in ids for m in json.loads(l)['logs'])))")
python3 $D/coverage.py $D $((SW0-60000)) $(date -u +%s%3N) > $D/coverage-prelive.json; cat $D/coverage-prelive.json; echo
while :; do python3 $D/evidence.py $D $((SW0-60000)) $(( $(date -u +%s%3N) - 15000 )) 9999999999999 true > $D/evidence-prelive.json 2>$D/evidence-prelive.err; rc=$?
  [ $rc -eq 4 ] && [ $(date +%s) -lt $((S0+170)) ] || break; sleep 3; done
jq -c '{result, mode, coverage:.coverage|{probes,covered,max_gap_ms,result}, gaps}' $D/evidence-prelive.json; [ $rc -eq 0 ] || abort "tail continuity not proven around the required sweeps (evidence rc=$rc: $(jq -c .lost $D/evidence-prelive.json 2>/dev/null))"
m=$(( ($(date +%s)/60) % 30 )); [ $m -ge 1 ] && [ $m -le 4 ] || abort "outside the hh:01-04 / hh:31-34 slot"
[ "$(hooks)" -eq 0 ] || abort "webhook seen before deploy"; shopcheck prelive || abort "Shopify changed before deploy"; d1check prelive || abort "D1 changed before deploy"
if [ "${PRELIVE_ONLY:-0}" = 1 ]; then echo "PRE-LIVE CHECKS PASSED at $(ts) UTC — PRELIVE_ONLY: gate.sh NOT run, nothing deployed, Worker stays $DRYVER DRY_RUN true"; exit 0; fi
$D/gate.sh 2>&1 | tee $D/gate-run.log; echo "--- gate.txt: $(cat $D/gate.txt)"; [ "$(cat $D/gate.txt)" = PASSED ] || exit 2
setsid nohup $D/guard.sh >/dev/null 2>&1 < /dev/null & sleep 4; cat $D/guard.log; kill -0 $(cat $D/guard.pid) && echo "guard running pid $(cat $D/guard.pid)"
wcheck live
shopcheck pre-go >/dev/null && d1check pre-go >/dev/null && [ "$(hooks)" -eq 0 ] || { echo "Shopify/D1 changed or webhook seen before GO -> strict restore"; echo "pre-GO check failed" > $D/manual-stop; exit 3; }
echo "Shopify + D1 unchanged, 0 webhooks at $(ts)"; date -u +%s%3N > $D/qag-start
echo "STRESS test armed at $(ts) UTC — READY FOR GO. $(cat $D/fire-plan.txt). First order must be seen within 1200s."
