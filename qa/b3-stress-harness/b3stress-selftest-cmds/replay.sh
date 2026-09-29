#!/bin/bash
# evidence.py against the REAL recorded tails of the aborted A5 attempt (frozen copy in b3stress-selftest/replay)
R=$1; TG=$(cat $R/tgate.txt); AR=$(cat $R/qag-start); ms() { date -u -d "2026-09-28 $1" +%s%3N; }; p=0; n=0
t() { n=$((n+1)); o=$(python3 $R/evidence.py $R $TG $(ms $2) $AR); rc=$?; if eval "$3"; then p=$((p+1)); echo "PASS replay: $1"; else echo "FAIL replay: $1 rc=$rc $o" | cut -c1-400; fi; }
t "window shorter than 12s -> WAIT (rc 3)" 15:02:20 '[ $rc = 3 ]'
t "16:02:12-16:13:40 BST: pre2 restart 16:02:16 is before qag-start (outside), coverage CONTINUOUS -> INTACT" 15:13:40 '[ $rc = 0 ] && echo "$o" | grep -q "\"tail\": \"pre2\", \"at\": \"15:02:16\", \"why\": \"process exited\", \"during_test\": false"'
t "through 16:14:05 BST: pre stalled + pre2 restarted 16:13:45, probes hb-699..701 missed -> LOST" 15:14:05 '[ $rc = 2 ] && echo "$o" | grep -q "hb-699" && echo "$o" | grep -q "both tails unavailable"'
t "through 16:15:40 BST: still LOST (18s blind spot stays in the window)" 15:15:40 '[ $rc = 2 ] && echo "$o" | grep -q "max_gap_ms 18000"'
echo "REPLAY: $p/$n passed"
