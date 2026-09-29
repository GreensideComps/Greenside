#!/usr/bin/env bash
# Stress competition registration in QA D1 (needs explicit approval). The allocator's own SQL from src/db.ts (UPSERT_COMPETITION as DRAFT,
# INSERT_POOL_ROW x capacity, MARK_POOL_BUILT) with ?n replaced by literals from stress.json. Aborts on any unexpected state.
set -u
S=__SCRATCHPAD__; D=$S/b3stress; R=$D/reg; A=$S/b3qa/greenside-entry-allocator; mkdir -p $R; cd $A
CFG=$D/stress.json; grep -q PLACEHOLDER_QAH $CFG && { echo "ABORT: stress.json competition_id not set"; exit 2; }
CID=$(jq -r .competition_id $CFG); PFX=$(jq -r .prefix $CFG); ST=$(jq -r .start_number $CFG); CAP=$(jq -r .capacity $CFG); H=$(jq -r .handle $CFG); TI=$(jq -r .title $CFG)
PAD=$(python3 -c "print(len(str($ST+$CAP-1)))")
abort() { echo "ABORT: $*"; exit 2; }
X() { npx wrangler d1 execute greenside_entries_qa --env qa --remote --json --command "$1" 2>/dev/null | python3 -c "import sys,json;t=sys.stdin.read();r=json.loads(t[t.index('['):]);print(json.dumps([x['results'] for x in r]))"; }
T=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ); echo "$T" > $R/t-register
echo "== 1. pre-registration snapshot"; $D/dsnap.sh $R/snap-pre.json || abort "snapshot"
PRE=$(X "SELECT (SELECT COUNT(*) FROM competition WHERE competition_id='$CID') a, (SELECT COUNT(*) FROM competition WHERE prefix='$PFX' COLLATE NOCASE) b, (SELECT COUNT(*) FROM entry_number WHERE entry_number LIKE '$PFX%' OR competition_id='$CID') c"); echo "   pre-checks $PRE"
[ "$PRE" = '[[{"a": 0, "b": 0, "c": 0}]]' ] || abort "id/prefix/numbers already present"
echo "== 2. UPSERT_COMPETITION (DRAFT) at $T"
O=$(X "INSERT INTO competition (
  competition_id, shop_domain, product_gid, prefix, start_number, capacity, pad_width,
  handle_snapshot, title_snapshot,
  skill_question_snapshot, skill_answers_snapshot, skill_answer_correct_snapshot,
  status, created_at, updated_at
) VALUES ('$CID', '7r5csb-1j.myshopify.com', 'gid://shopify/Product/$CID', '$PFX', $ST, $CAP, $PAD, '$H', '$TI', NULL, NULL, NULL, 'DRAFT', '$T', '$T')
ON CONFLICT (competition_id) DO UPDATE SET
  handle_snapshot = excluded.handle_snapshot,
  title_snapshot  = excluded.title_snapshot,
  skill_question_snapshot = excluded.skill_question_snapshot,
  skill_answers_snapshot  = excluded.skill_answers_snapshot,
  skill_answer_correct_snapshot = excluded.skill_answer_correct_snapshot,
  updated_at = excluded.updated_at
RETURNING *"); echo "   $O"; echo "$O" | grep -q '"status": "DRAFT"' || abort "competition insert"
echo "== 3. INSERT_POOL_ROW x$CAP"
Q=""; for s in $(seq $ST $((ST+CAP-1))); do Q="$Q INSERT INTO entry_number (competition_id, seq, entry_number, status, allocation_seq) VALUES ('$CID', $s, '$PFX$(printf "%0${PAD}d" $s)', 'AVAILABLE', 0);"; done
X "$Q" >/dev/null; N=$(X "SELECT COUNT(*) n FROM entry_number WHERE competition_id='$CID'"); echo "   pool rows $N"; [ "$N" = "[[{\"n\": $CAP}]]" ] || abort "pool not exactly $CAP"
echo "== 4. MARK_POOL_BUILT (DRAFT -> OPEN)"
O=$(X "UPDATE competition SET status = 'OPEN', pool_built_at = '$T', updated_at = '$T'
 WHERE competition_id = '$CID' AND status = 'DRAFT'
RETURNING *"); echo "   $O"; echo "$O" | grep -q '"status": "OPEN"' || abort "open"
echo "== 5. post-registration snapshot"; $D/dsnap.sh $R/snap-post.json || abort "snapshot"
echo "== 6. verify"; python3 - "$R" "$CFG" "$T" <<'PY'
import json, sys
R, cfgp, T = sys.argv[1:]; cfg = json.load(open(cfgp)); C = cfg['competition_id']
a = json.load(open(f'{R}/snap-pre.json')); b = json.load(open(f'{R}/snap-post.json')); L = lambda d, k: d[k] if not isinstance(d[k], str) else json.loads(d[k])
dev = [k for k in a if k not in ('competitions', 'entries') and a[k] != b[k]]
ca = {c['competition_id']: c for c in L(a, 'competitions')}; cb = {c['competition_id']: c for c in L(b, 'competitions')}
dev += [f'competition {k} changed' for k in ca if json.dumps(ca[k], sort_keys=True) != json.dumps(cb.get(k), sort_keys=True)]
if set(cb) - set(ca) != {C}: dev.append('new competitions ' + str(set(cb) - set(ca)))
q = cb.get(C) or {}; pad = len(str(cfg['start_number'] + cfg['capacity'] - 1))
exp = {'competition_id': C, 'shop_domain': '7r5csb-1j.myshopify.com', 'product_gid': cfg['product_gid'], 'prefix': cfg['prefix'], 'start_number': cfg['start_number'], 'capacity': cfg['capacity'], 'pad_width': pad,
       'handle_snapshot': cfg['handle'], 'title_snapshot': cfg['title'], 'skill_question_snapshot': None, 'skill_answers_snapshot': None, 'skill_answer_correct_snapshot': None,
       'status': 'OPEN', 'pool_built_at': T, 'frozen_at': None, 'created_at': T, 'updated_at': T}
if q != exp: dev.append(f'row {q}')
old = [e for e in L(b, 'entries') if e[0] != C]; new = [e for e in L(b, 'entries') if e[0] == C]
if old != L(a, 'entries'): dev.append('existing entries changed')
if new != [[C, s, f"{cfg['prefix']}{str(s).zfill(pad)}", 'AVAILABLE', None, None, None, None, 0, None, None, None] for s in range(cfg['start_number'], cfg['start_number'] + cfg['capacity'])]: dev.append(f'pool {new}')
i = L(b, 'integrity'); dev += [f'integrity {k}={v}' for k, v in i.items() if k not in ('events_total', 'allocations_total') and v]
print('   pool:', [e[2] + '/' + e[3] for e in new]); print('   integrity:', i)
print('RESULT:', f"EXACT {1 + cfg['capacity']}-ROW ADDITION" if not dev else 'DEVIATIONS ' + str(dev)); sys.exit(2 if dev else 0)
PY
