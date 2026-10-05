#!/bin/bash
# Full offline + read-only suite for the B3 stress harness (v3 = Workers Logs gap recovery). Writes $O/final-*.out
S=__SCRATCHPAD__; O=$S/b3stress-selftest-cmds; cd $S
bash $O/cmd1.sh > $O/final-stress.out 2>&1
bash $O/cmd2.sh > $O/final-coverage.out 2>&1
R=$S/b3stress-replay; cp $S/b3stress/evidence.py $S/b3stress/coverage.py $R/; rm -f $R/gaps-*.json $R/obs.jsonl
OBS_DISABLE=1 bash $O/replay.sh $R > $O/final-replay-legacy.out 2>&1
# --no-real: the historical REAL checks (A13/A14/C1-C5) query fixed 28 Sep 2026 windows that have aged out of Workers Logs retention.
# Live coverage is the opt-in b3stress-selftest-cmds/wlreal_recent.py (rolling window), run separately.
python3 $O/wltest.py $S/b3stress $S/b3obs $S/b3stress-wltest $S/b3stress-replay --no-real > $O/final-wl.out 2>&1
OBS_DISABLE=1 python3 $O/guardtest.py $S/b3stress/guard.sh $S/b3stress $S/b3stress-guardsandbox > $O/final-guard-legacy.out 2>&1
python3 $O/guardtest_wl.py $S/b3stress/guard.sh $S/b3stress $S/b3obs $S/b3stress-guardwl > $O/final-guard-wl.out 2>&1
python3 $O/firetest.py $S/b3stress > $O/final-fire.out 2>&1
echo ALL-DONE > $O/final-done
