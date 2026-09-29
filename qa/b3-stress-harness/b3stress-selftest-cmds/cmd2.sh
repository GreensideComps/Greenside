N=__SCRATCHPAD__/b3stress; T=$N/../b3stress-selftest/cov; rm -rf $T; mkdir -p $T; python3 - "$N" "$T" <<'EOF'
import json, subprocess, sys, datetime
N, T = sys.argv[1:]; base = int(datetime.datetime(2026, 9, 28, 14, 0, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)
def run(name, pre_skip, pre2_skip, want):
    hb = []; p1 = []; p2 = []
    for i in range(60):
        ts = base + 4000 * i; tag = f'hb-{i}'
        hb.append(f"{datetime.datetime.utcfromtimestamp(ts/1000).strftime('%H:%M:%S')} {tag} dry_run=true")
        ev = json.dumps({'eventTimestamp': ts, 'event': {'request': {'url': f'https://x.dev/health?probe={tag}'}}})
        if i not in pre_skip: p1.append(ev)
        if i not in pre2_skip: p2.append(ev)
    open(f'{T}/heartbeat.log', 'w').write('\n'.join(hb) + '\n'); open(f'{T}/pre.jsonl', 'w').write('\n'.join(p1)); open(f'{T}/pre2.jsonl', 'w').write('\n'.join(p2))
    r = subprocess.run(['python3', f'{N}/coverage.py', T, str(base), str(base + 4000 * 59)], capture_output=True, text=True); o = json.loads(r.stdout)
    print('PASS' if (r.returncode == 0) == want else 'FAIL', name, o['result'], o['max_gap_ms'], o['by_tail'])
run('both tails complete', set(), set(), True)
run('pre drops 20-30, pre2 covers', set(range(20, 31)), set(), True)
run('pre drops 20-30, pre2 drops 40-45 (never both)', set(range(20, 31)), set(range(40, 46)), True)
run('both drop 25-28 (real blind spot)', set(range(20, 31)), set(range(25, 29)), False)
run('single probe lost in both', {33}, {33}, False)
EOF