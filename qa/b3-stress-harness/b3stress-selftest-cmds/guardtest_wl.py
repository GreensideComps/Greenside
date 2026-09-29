#!/usr/bin/env python3
"""End-to-end sandbox: the REAL guard.sh + REAL evidence.py/coverage.py/obsq.py, stubs for D1/Shopify/deploy, synthetic tails and a
SIMULATED Workers Logs store (OBSQ_FAKE). OBS_WAIT_S=20 shortens the 60s wait for test time only (production default 60 is tested
in wltest B8e). Usage: guardtest_wl.py GUARD_SH N_DIR OBS_DIR OUT_DIR"""
import json, os, shutil, subprocess, sys, threading, time, datetime
GUARD, N, OBS, T = sys.argv[1:5]
now_ms = lambda: int(time.time() * 1000); hms = lambda ms: datetime.datetime.utcfromtimestamp(ms / 1000).strftime('%H:%M:%S')
SVC = 'greenside-entry-allocator-qa'
STUBS = {
 'dsnap.sh': '#!/bin/bash\necho {} > "$1"\n', 'shopsnap.sh': '#!/bin/bash\necho {} > "$1"\n',
 'dstate.py': '#!/usr/bin/env python3\nimport json,os,sys\nD=os.path.dirname(os.path.abspath(sys.argv[0]))\nc=os.path.exists(D+"/f_complete")\nprint(json.dumps({"orders":[],"complete":c,"allocated":6 if c else 0,"allocations":4 if c else 0,"new_events":6 if c else 0,"deviations":[]}))\n',
 'shopstate.py': '#!/usr/bin/env python3\nimport json,os,sys\nD=os.path.dirname(os.path.abspath(sys.argv[0]))\nc=os.path.exists(D+"/f_complete")\nprint(json.dumps({"new_orders":[],"complete":c,"stock":[0,0] if c else [6,6],"stock_expected":0,"deviations":[]}))\n',
 'cstate.py': '#!/usr/bin/env python3\nimport json,os,sys\nD=sys.argv[1]\nc=os.path.exists(D+"/f_complete")\nprint(json.dumps({"hooks":[],"sweeps":[],"deviations":[],"hooks_done":c,"zero_sweeps_after_complete":1 if c else 0,"per_topic":{}}))\n',
 'overlap.py': '#!/usr/bin/env python3\nprint(\'{"verdict":"CONCURRENT","counts":{"cross_order":1},"peak_in_flight":2}\')\n',
 'restore.sh': '#!/bin/bash\nD=$(dirname "$0"); echo "stub restore"; echo PASSED > $D/restoregate.txt\n',
 'postcheck.sh': '#!/bin/bash\nD=$(dirname "$0"); echo "stub postcheck"; echo PASSED > $D/postcheck.txt\n'}
class Rig:
    def __init__(s, name):
        s.d = f'{T}/{name}'; shutil.rmtree(s.d, ignore_errors=True); os.makedirs(s.d)
        for f in ('evidence.py', 'coverage.py', 'stress.json'): shutil.copy(f'{N}/{f}', s.d)
        for f, body in STUBS.items(): open(f'{s.d}/{f}', 'w').write(body); os.chmod(f'{s.d}/{f}', 0o755)
        g = open(GUARD).read().replace('D=__SCRATCHPAD__/b3stress;', f'D={s.d};', 1)
        assert f'D={s.d};' in g; open(f'{s.d}/guard.sh', 'w').write(g); os.chmod(f'{s.d}/guard.sh', 0o755)
        for t in ('pre', 'pre2', 'conf'): open(f'{s.d}/{t}.restarts', 'w').write('0\n'); open(f'{s.d}/{t}.jsonl', 'w').close()
        open(f'{s.d}/supervisor.log', 'w').close(); open(f'{s.d}/heartbeat.log', 'w').close(); open(f'{s.d}/newver.txt', 'w').write('ffffffff\n')
        s.procs = {t: subprocess.Popen(['sleep', '900']) for t in ('pre', 'pre2')}
        for t, p in s.procs.items(): open(f'{s.d}/{t}.pid', 'w').write(str(p.pid))
        s.down = set(); s.wl_down = False; s.n = 0; s.stop = False; s.wl = []; s.lock = threading.Lock(); s.flush()
        s.w = threading.Thread(target=s.writer, daemon=True); s.w.start()
    def flush(s):
        with s.lock: json.dump({'records': s.wl}, open(f'{s.d}/wl.tmp', 'w')); os.replace(f'{s.d}/wl.tmp', f'{s.d}/wlstore.json')
    def writer(s):
        while not s.stop:
            s.n += 1; ts = now_ms(); tag = f'hb-{s.n}'; ray = f'r{ts}'
            with open(f'{s.d}/heartbeat.log', 'a') as h: h.write(f'{hms(ts)} {tag} dry_run=false\n')
            time.sleep(0.3)
            ev = {'request': {'url': f'https://x.dev/health?probe={tag}', 'headers': {'cf-ray': ray}}, 'response': {'status': 200}}
            for t in ('pre', 'pre2'):
                if t not in s.down:
                    with open(f'{s.d}/{t}.jsonl', 'a') as f: f.write(json.dumps({'eventTimestamp': ts, 'wallTime': 1, 'outcome': 'ok', 'scriptVersion': {'id': 'ffffffff'}, 'exceptions': [], 'logs': [], 'event': ev}, indent=2) + '\n')
            if not s.wl_down:
                with s.lock: s.wl.append({'timestamp': ts + 1, '$metadata': {'id': f'id{ts:015d}', 'requestId': f'q{ts}', 'service': SVC, 'type': 'cf-worker-event'},
                                          '$workers': {'eventType': 'fetch', 'scriptName': SVC, 'scriptVersion': {'id': 'ffffffff'}, 'outcome': 'ok', 'wallTimeMs': 1, 'cpuTimeMs': 0, 'truncated': False,
                                                       'event': {**ev, 'search': {'probe': tag}}}})
                s.flush()
            time.sleep(1.7)
    def arm(s): open(f'{s.d}/qag-start', 'w').write(str(now_ms()))
    def start_guard(s):
        open(f'{s.d}/tgate.txt', 'w').write(str(now_ms() - 1000))
        env = dict(os.environ, OBSQ_FAKE=f'{s.d}/wlstore.json', OBSQ_DIR=OBS, OBS_WAIT_S='20'); env.pop('OBS_DISABLE', None)
        s.g = subprocess.Popen(['bash', f'{s.d}/guard.sh'], stdout=subprocess.DEVNULL, stderr=open(f'{s.d}/guard.stderr', 'w'), env=env)
    def wait(s, sec):
        end = time.time() + sec
        while time.time() < end and s.g.poll() is None: time.sleep(0.5)
        return s.g.poll()
    def log(s): return open(f'{s.d}/guard.log').read()
    def done(s):
        s.stop = True
        if s.g.poll() is None: s.g.kill()
        for p in s.procs.values():
            if p.poll() is None: p.kill(); p.wait()
res = []
def chk(name, ok, info=''): res.append(bool(ok)); print(f"{'PASS' if ok else 'FAIL'} guard+WL: {name}  {info}", flush=True)
L = lambda r, pat: ' | '.join(l[9:200] for l in r.log().splitlines() if pat in l)[:600]

r = Rig('w1'); time.sleep(6); r.start_guard(); r.arm(); time.sleep(4)
r.down.update(('pre', 'pre2')); time.sleep(9); r.down.clear(); time.sleep(1); open(f'{r.d}/f_complete', 'w').close()
rc = r.wait(110); r.done(); lines = r.log().splitlines()
ix = lambda pat: next((i for i, l in enumerate(lines) if pat in l), -1)
chk('W1 real tail gap during the test, Workers Logs has it: PENDING (completion held) -> RECOVERED -> EVIDENCE INTACT -> planned restore, no STOP',
    rc == 0 and 'STOP' not in r.log() and 0 <= ix('"PENDING"') < ix('"RECOVERED"') < ix('EVIDENCE INTACT') < ix('stress window done') and ix('holding the planned restore') >= 0
    and open(f'{r.d}/guard-result.txt').read().strip() == 'PLANNED', L(r, 'tail gaps')[:500])
r = Rig('w2'); time.sleep(6); r.start_guard(); r.arm(); time.sleep(4)
r.down.update(('pre', 'pre2')); r.wl_down = True; time.sleep(9); r.down.clear(); r.wl_down = False
rc = r.wait(80); r.done()
chk('W2 tail gap that Workers Logs does NOT have -> STOP EVIDENCE LOST at the deadline -> strict restore', rc == 2 and 'STOP: EVIDENCE LOST' in r.log() and 'missing from Workers Logs' in r.log() and 'stub restore' in r.log(), L(r, 'STOP'))
r = Rig('w3'); time.sleep(6); r.start_guard(); r.arm(); time.sleep(4)
r.down.update(('pre', 'pre2')); rc = r.wait(75); r.done()
chk('W3 tails stay blind longer than the wait (open gap) -> STOP EVIDENCE LOST (Workers Logs never substitutes for absent tails)', rc == 2 and 'open gap' in r.log(), L(r, 'STOP'))
r = Rig('w4'); time.sleep(6); r.start_guard(); r.arm(); time.sleep(4)
for p in r.procs.values(): p.kill(); p.wait()
rc = r.wait(12); r.done()
chk('W4 both tail processes down -> immediate STOP (existing abort, not overridden by Workers Logs)', rc == 2 and 'BOTH TAILS DOWN' in r.log(), L(r, 'STOP'))
print(f'GUARD+WL SANDBOX: {sum(res)}/{len(res)} passed', flush=True)
