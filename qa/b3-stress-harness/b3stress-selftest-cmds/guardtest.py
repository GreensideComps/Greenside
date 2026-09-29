#!/usr/bin/env python3
"""Offline sandbox test of guard.sh's tail/evidence logic. The REAL guard.sh (path rewritten), evidence.py and coverage.py run
against stubs for every D1/Shopify/deploy script, with synthetic heartbeat probes and two synthetic tails. Nothing touches the network.
Usage: guardtest.py GUARD_SH N_DIR OUT_DIR [scenario ...]"""
import json, os, shutil, subprocess, sys, threading, time, datetime
GUARD, N, T = sys.argv[1], sys.argv[2], sys.argv[3]; only = sys.argv[4:]
now_ms = lambda: int(time.time() * 1000)
hms = lambda ms: datetime.datetime.utcfromtimestamp(ms / 1000).strftime('%H:%M:%S')
STUBS = {
 'dsnap.sh': '#!/bin/bash\necho {} > "$1"\n',
 'shopsnap.sh': '#!/bin/bash\necho {} > "$1"\n',
 'dstate.py': '#!/usr/bin/env python3\nimport json,os,sys\nD=os.path.dirname(os.path.abspath(sys.argv[0]))\nc=os.path.exists(D+"/f_complete")\nprint(json.dumps({"orders":[],"complete":c,"allocated":6 if c else 0,"allocations":4 if c else 0,"new_events":6 if c else 0,"deviations":[]}))\n',
 'shopstate.py': '#!/usr/bin/env python3\nimport json,os,sys\nD=os.path.dirname(os.path.abspath(sys.argv[0]))\nc=os.path.exists(D+"/f_complete")\nprint(json.dumps({"new_orders":[],"complete":c,"stock":[0,0] if c else [6,6],"stock_expected":0,"deviations":[]}))\n',
 'cstate.py': '#!/usr/bin/env python3\nimport json,os,sys\nD=sys.argv[1]\nc=os.path.exists(D+"/f_complete")\nprint(json.dumps({"hooks":[],"sweeps":[],"deviations":[],"hooks_done":c,"zero_sweeps_after_complete":1 if c else 0,"per_topic":{}}))\n',
 'overlap.py': '#!/usr/bin/env python3\nprint(\'{"verdict":"CONCURRENT","counts":{"cross_order":1},"peak_in_flight":2}\')\n',
 'restore.sh': '#!/bin/bash\nD=$(dirname "$0"); echo "stub restore"; echo PASSED > $D/restoregate.txt\n',
 'postcheck.sh': '#!/bin/bash\nD=$(dirname "$0"); echo "stub postcheck"; echo PASSED > $D/postcheck.txt\n',
}
class Rig:
    def __init__(s, name, guard=GUARD):
        s.d = f'{T}/{name}'; shutil.rmtree(s.d, ignore_errors=True); os.makedirs(s.d)
        for f in ('evidence.py', 'coverage.py', 'stress.json'): shutil.copy(f'{N}/{f}', s.d)
        for f, body in STUBS.items(): open(f'{s.d}/{f}', 'w').write(body); os.chmod(f'{s.d}/{f}', 0o755)
        g = open(guard).read().replace('D=__SCRATCHPAD__/b3stress;', f'D={s.d};', 1)
        assert f'D={s.d};' in g; open(f'{s.d}/guard.sh', 'w').write(g); os.chmod(f'{s.d}/guard.sh', 0o755)
        for t in ('pre', 'pre2', 'conf'): open(f'{s.d}/{t}.restarts', 'w').write('0\n'); open(f'{s.d}/{t}.jsonl', 'w').close()
        open(f'{s.d}/supervisor.log', 'w').close(); open(f'{s.d}/heartbeat.log', 'w').close(); open(f'{s.d}/newver.txt', 'w').write('ffffffff\n')
        s.procs = {t: subprocess.Popen(['sleep', '600']) for t in ('pre', 'pre2')}
        for t, p in s.procs.items(): open(f'{s.d}/{t}.pid', 'w').write(str(p.pid))
        s.down = set(); s.n = 0; s.stop = False; s.t0 = now_ms()
        s.w = threading.Thread(target=s.writer, daemon=True); s.w.start()
    def writer(s):  # heartbeat probe every 2s; each live tail records it ~0.3s later
        while not s.stop:
            s.n += 1; ts = now_ms(); tag = f'hb-{s.n}'
            with open(f'{s.d}/heartbeat.log', 'a') as h: h.write(f'{hms(ts)} {tag} dry_run=false\n')
            time.sleep(0.3)
            for t in ('pre', 'pre2'):
                if t not in s.down:
                    with open(f'{s.d}/{t}.jsonl', 'a') as f: f.write(json.dumps({'eventTimestamp': ts, 'wallTime': 1, 'event': {'request': {'url': f'https://x.dev/health?probe={tag}'}}}, indent=2) + '\n')
            time.sleep(1.7)
    def restart(s, tail, at_ms=None):
        at = at_ms or now_ms(); r = int(open(f'{s.d}/{tail}.restarts').read()) + 1
        with open(f'{s.d}/supervisor.log', 'a') as f: f.write(f'{hms(at)} RESTART {tail} (process exited) -> restarts={r}\n{hms(at)} start {tail} pid 1\n')
        open(f'{s.d}/{tail}.restarts', 'w').write(f'{r}\n')
    def arm(s, at_ms=None): open(f'{s.d}/qag-start', 'w').write(str(at_ms or now_ms()))
    def start_guard(s):
        open(f'{s.d}/tgate.txt', 'w').write(str(now_ms() - 1000))
        s.g = subprocess.Popen(['bash', f'{s.d}/guard.sh'], stdout=subprocess.DEVNULL, stderr=open(f'{s.d}/guard.stderr', 'w'))
    def wait(s, sec):
        end = time.time() + sec
        while time.time() < end and s.g.poll() is None: time.sleep(0.5)
        return s.g.poll()
    def log(s): return open(f'{s.d}/guard.log').read()
    def done(s):
        s.stop = True
        if s.g.poll() is None: s.g.kill()
        for p in s.procs.values():
            if p.poll() is None: p.kill()
res = []
def chk(name, ok, info=''): res.append(ok); print(f"{'PASS' if ok else 'FAIL'} guard: {name}  {info}", flush=True)
def last(r, pat): return ' | '.join(l[9:170] for l in r.log().splitlines() if pat in l)[:400]
def scen(name): return not only or name in only

if scen('race'):  # restart 2s BEFORE qag-start, noticed by the guard only after arming -> outside the test, no stop
    r = Rig('race'); time.sleep(6); r.start_guard(); time.sleep(4)
    r.down.add('pre2'); time.sleep(2.5); r.down.discard('pre2'); a = now_ms(); r.arm(a); time.sleep(0.2); r.restart('pre2', a - 2000)
    rc = r.wait(25); r.done()
    chk('restart before qag-start detected after arming is outside the test (no stop)', rc is None and 'before qag-start: outside the test' in r.log() and 'STOP' not in r.log(), last(r, 'restart'))
if scen('tolerated'):  # during the test pre is down 9s and restarts; pre2 covers every probe -> tolerated, continue
    r = Rig('tolerated'); time.sleep(6); r.start_guard(); r.arm(); time.sleep(4)
    r.down.add('pre'); time.sleep(9); r.restart('pre'); r.down.discard('pre')
    rc = r.wait(28)
    ev = None  # read while the guard is still running: it rewrites evidence.json every loop, so retry until a complete JSON is read
    for _ in range(40):
        try: ev = json.load(open(f'{r.d}/evidence.json')); break
        except (ValueError, FileNotFoundError): time.sleep(0.25)
    r.done()
    chk('one tail restarts during the test, the other covers everything -> continue', rc is None and 'during the test: tolerated' in r.log() and 'STOP' not in r.log() and ev['result'] == 'INTACT' and ev['coverage']['result'] == 'CONTINUOUS', f"evidence {ev['result']} {ev['coverage']['result']} restarts={ev['restarts']}")
if scen('tolerated-old'):  # the same scenario on the OLD guard: stops on the restart itself
    r = Rig('tolerated-old', guard=f'{N}/harness-v2-before/guard.sh'); time.sleep(6); r.start_guard(); r.arm(); time.sleep(4)
    r.down.add('pre'); time.sleep(9); r.restart('pre'); r.down.discard('pre')
    rc = r.wait(20); r.done()
    chk('(before/after) the OLD guard stops on the same tolerated restart', 'TAIL RESTART DURING TEST' in r.log(), last(r, 'STOP'))
if scen('lost'):  # both tails blind for 9s (probes missed) -> EVIDENCE LOST stop
    r = Rig('lost'); time.sleep(6); r.start_guard(); r.arm(); time.sleep(4)
    r.down.update(('pre', 'pre2')); time.sleep(9); r.down.clear()
    rc = r.wait(35); r.done()
    chk('probes missed by both tails -> STOP EVIDENCE LOST -> strict restore', rc == 2 and 'STOP: EVIDENCE LOST' in r.log() and 'coverage GAP' in r.log() and 'stub restore' in r.log(), last(r, 'STOP'))
if scen('overlap'):  # pre misses probe k, pre2 misses probe k+1 (both restart): every probe still covered, but both unavailable at once
    r = Rig('overlap'); time.sleep(6); r.start_guard(); r.arm(); time.sleep(4)
    n0 = r.n
    while r.n == n0: time.sleep(0.05)
    r.down.add('pre'); r.restart('pre'); time.sleep(1.9); r.down.add('pre2'); r.down.discard('pre'); r.restart('pre2'); time.sleep(2.0); r.down.discard('pre2')
    rc = r.wait(35); r.done()
    chk('both tails unavailable at the same time (no single probe missed) -> STOP', rc == 2 and 'both tails unavailable' in r.log() and 'coverage GAP' not in r.log(), last(r, 'STOP'))
if scen('gap'):  # heartbeat probes stop for 15s (no evidence of tail liveness beyond the 12s limit) -> STOP
    r = Rig('gap'); time.sleep(6); r.start_guard(); r.arm(); time.sleep(4)
    r.stop = True; time.sleep(15); r.stop = False; r.w = threading.Thread(target=r.writer, daemon=True); r.w.start()
    rc = r.wait(30); r.done()
    chk('evidence gap beyond the existing 12s limit -> STOP (heartbeat stale or evidence lost)', rc == 2 and ('EVIDENCE LOST' in r.log() or 'HEARTBEAT STALE' in r.log()), last(r, 'STOP'))
if scen('bothdown'):  # both tail processes gone -> immediate STOP
    r = Rig('bothdown'); time.sleep(6); r.start_guard(); r.arm(); time.sleep(4)
    for p in r.procs.values(): p.kill(); p.wait()  # reap: a real supervisor child is reaped by its bash parent
    rc = r.wait(10); r.done()
    chk('both tail processes down -> immediate STOP', rc == 2 and 'BOTH TAILS DOWN' in r.log(), last(r, 'STOP'))
if scen('complete'):  # completion -> held until evidence covers the completion moment -> planned restore
    r = Rig('complete'); time.sleep(6); r.start_guard(); r.arm(); time.sleep(4); open(f'{r.d}/f_complete', 'w').close(); tc = time.time()
    rc = r.wait(45); r.done(); held = [l for l in r.log().splitlines() if 'holding the planned restore' in l]
    lines = r.log().splitlines(); i_h = next((i for i, l in enumerate(lines) if 'holding' in l), -1); i_e = next((i for i, l in enumerate(lines) if 'EVIDENCE INTACT' in l), -1)
    chk('completion is held until evidence covers it, then planned restore', rc == 0 and held and 0 <= i_h < i_e and 'stress window done -> planned strict restore' in r.log() and open(f'{r.d}/guard-result.txt').read().strip() == 'PLANNED', f"{last(r, 'holding')} || {last(r, 'EVIDENCE INTACT')[:200]}")
if scen('complete-lost'):  # evidence lost during the hold -> STOP, not a planned restore
    r = Rig('complete-lost'); time.sleep(6); r.start_guard(); r.arm(); time.sleep(4)
    r.down.update(('pre', 'pre2')); time.sleep(0.5); open(f'{r.d}/f_complete', 'w').close(); time.sleep(9); r.down.clear()
    rc = r.wait(40); r.done()
    chk('evidence lost around completion -> STOP, no planned restore', rc == 2 and 'EVIDENCE LOST' in r.log() and 'planned-restore' not in os.listdir(r.d), last(r, 'STOP'))
print(f'GUARD SANDBOX: {sum(res)}/{len(res)} passed', flush=True)
