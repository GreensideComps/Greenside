#!/usr/bin/env bash
# H6 L1 QA D1 registration (Stage 3 only, separately approved; NOT run in Stage 1).
#   register.sh COMPETITION_ID ALLOCATOR_DIR OUT_DIR REGISTER-QAL-2000-ENTRIES
# ALLOCATOR_DIR: a checkout of greenside-entry-allocator (its wrangler.toml); every wrangler command uses --env qa (QA D1 only).
# Order: phrase -> read-only prechecks (no row for this id, prefix QAL or any QAL number) -> regsql.py files -> apply in order
# -> exact verification (1 competition OPEN, capacity 2000, 2000 pool rows QAL1001..QAL3000 all AVAILABLE/allocation_seq 0).
# Any unexpected state aborts. Re-running after a partial apply is refused by the precheck (rows exist): inspect by hand.
set -u
CID="${1:?competition id}"; A="${2:?allocator dir}"; OUT="${3:?out dir}"; PHRASE="${4:-}"
H="$(cd "$(dirname "$0")" && pwd)"
[ "$PHRASE" = "REGISTER-QAL-2000-ENTRIES" ] || { echo "REFUSED: confirmation phrase missing or wrong"; exit 2; }
abort() { echo "ABORT: $*"; exit 2; }
X() { (cd "$A" && npx wrangler d1 execute greenside_entries_qa --env qa --remote --json "$@" 2>/dev/null) | python3 -c "import sys,json;t=sys.stdin.read();r=json.loads(t[t.index('['):]);print(json.dumps([x['results'] for x in r]))"; }
T=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
PRE=$(X --command "SELECT (SELECT COUNT(*) FROM competition WHERE competition_id='$CID') a, (SELECT COUNT(*) FROM competition WHERE prefix='QAL' COLLATE NOCASE) b, (SELECT COUNT(*) FROM entry_number WHERE entry_number LIKE 'QAL%' OR competition_id='$CID') c")
[ "$PRE" = '[[{"a": 0, "b": 0, "c": 0}]]' ] || abort "id/prefix/numbers already present: $PRE"
python3 "$H/regsql.py" write --competition "$CID" --out "$OUT" --at "$T" || abort "regsql"
for f in $(ls "$OUT"/*.sql | sort); do
  echo "== apply $(basename "$f") ($(wc -c < "$f") bytes)"
  X --file "$f" > /dev/null || abort "apply $(basename "$f")"
done
V=$(X --command "SELECT (SELECT status FROM competition WHERE competition_id='$CID') s, (SELECT capacity FROM competition WHERE competition_id='$CID') cap, COUNT(*) n, SUM(status='AVAILABLE') av, SUM(allocation_seq) asq, MIN(seq) lo, MAX(seq) hi, MIN(entry_number) e1, MAX(entry_number) e2 FROM entry_number WHERE competition_id='$CID'")
echo "verify: $V"
[ "$V" = '[[{"s": "OPEN", "cap": 2000, "n": 2000, "av": 2000, "asq": 0, "lo": 1001, "hi": 3000, "e1": "QAL1001", "e2": "QAL3000"}]]' ] || abort "registration not exact"
echo "REGISTERED: $CID QAL1001..QAL3000 (2000 AVAILABLE) at $T"
