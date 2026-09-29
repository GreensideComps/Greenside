#!/usr/bin/env bash
# After a restore: wait (max 1500s) for the first cron sweep on the restored version that starts after the restore; it must be a
# clean dry run (all zeros, on the restored version, no other logs). Then D1 must equal the last live-window snapshot, integrity 0.
# Writes postcheck.txt PASSED|FAILED, then stops the monitors.
D=__SCRATCHPAD__/b3stress; ts() { date -u +%T; }
RV=$(cat $D/restorever.txt); T0=$(cat $D/trestore.txt); END=$(( $(date +%s) + 1500 )); echo "=== postcheck: waiting for a dry-run sweep on ${RV:0:8} ($(ts)) ==="
R=3; while [ $(date +%s) -lt $END ]; do python3 - "$D" "$T0" "$RV" > $D/postcheck.json <<'PY'
import json, subprocess, sys
D, t0, rv = sys.argv[1], int(sys.argv[2]), sys.argv[3]
sw = [json.loads(l) for l in subprocess.run(['python3', f'{D}/sweeps.py', D, str(t0)], capture_output=True, text=True).stdout.splitlines() if l.strip()]
sw = [s for s in sw if (s.get('scheduledTime') or s['ts']) > t0 and any(isinstance(m, dict) and m.get('event') == 'reconcile_summary' for m in s['logs'])]
if not sw: print(json.dumps({'result': 'WAIT'})); sys.exit(3)
s = sw[0]; su = next(m for m in s['logs'] if isinstance(m, dict) and m.get('event') == 'reconcile_summary')
other = [m for m in s['logs'] if not isinstance(m, dict) or m.get('event') not in ('reconcile_started', 'reconcile_summary')]
exp = {'dry_run': True, 'report': True, 'mismatched': 0, 'converged_orders': 0, 'claimed': 0, 'released': 0, 'refused_not_open': 0, 'unreadable': 0, 'errors': 0, 'truncated': False, 'aged_held': 0}
bad = {k: su.get(k) for k, v in exp.items() if su.get(k) != v}
if s['version'] != rv: bad['version'] = s['version']
if s['outcome'] != 'ok' or s['exceptions']: bad['outcome'] = s['outcome']
if other: bad['other_logs'] = other
print(json.dumps({'result': 'FAIL' if bad else 'PASS', 'cron': s['cron'], 'run_id': su.get('run_id'), 'mode': su.get('mode'), 'in_scope': su.get('in_scope'), 'bad': bad})); sys.exit(2 if bad else 0)
PY
R=$?; [ $R -ne 3 ] && break; sleep 10; done
cat $D/postcheck.json; echo
OK=1; [ $R -eq 0 ] || { echo "    post-restore dry-run sweep NOT clean (rc=$R)"; OK=0; }
$D/dsnap.sh $D/d1-post.json || { echo "    D1 snapshot failed"; OK=0; }
python3 -c "
import json,sys;a=json.load(open('$D/d1-final.json'));b=json.load(open('$D/d1-post.json'));i=json.loads(b['integrity']) if isinstance(b['integrity'],str) else b['integrity']
bad={k:v for k,v in i.items() if k not in ('events_total','allocations_total') and v!=0};print('    D1 identical to last live snapshot:',a==b,'| integrity',i)
sys.exit(0 if a==b and not bad else 2)" || OK=0
[ $OK = 1 ] && echo PASSED > $D/postcheck.txt || echo FAILED > $D/postcheck.txt; echo "POSTCHECK $(cat $D/postcheck.txt) ($(ts))"; touch $D/stop
