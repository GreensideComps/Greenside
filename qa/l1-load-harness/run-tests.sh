#!/usr/bin/env bash
# L1 offline test runner (Stage 1). No network, no Shopify, no D1, no Worker.
#   run-tests.sh          unit tests + syntax + secret scan
#   run-tests.sh --mutation   ...plus the mutation check (several minutes)
set -u
H="$(cd "$(dirname "$0")" && pwd)"; rc=0
echo "== L1 unit tests"
(cd "$H/tests" && python3 -W ignore::ResourceWarning -m unittest test_l1 2>&1 | tail -3) | tee /dev/stderr | grep -q '^OK' || rc=1
echo "== syntax"
for f in "$H"/l1/*.py "$H"/tests/*.py; do python3 -m py_compile "$f" || { echo "PY FAIL $f"; rc=1; }; done
for f in "$H"/l1/*.sh "$H"/run-tests.sh; do bash -n "$f" || { echo "SH FAIL $f"; rc=1; }; done
find "$H" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
echo "   python $(ls "$H"/l1/*.py "$H"/tests/*.py | wc -l), shell $(ls "$H"/l1/*.sh "$H"/run-tests.sh | wc -l): OK unless listed above"
echo "== secret scan (values of credential-like environment variables, and known token patterns)"
python3 - "$H" <<'PY' || rc=1
import os, re, sys
names = [k for k in os.environ if any(t in k.upper() for t in ("TOKEN", "SECRET", "KEY", "PASSWORD", "CLIENT_ID"))]
vals = [os.environ[k] for k in names if len(os.environ[k]) >= 12]
pat = re.compile(r"shpat_[0-9a-f]{20,}|shpss_[0-9a-f]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY")
hits, n = 0, 0
for root, _, files in os.walk(sys.argv[1]):
    for f in files:
        p = os.path.join(root, f); n += 1
        t = open(p, "rb").read().decode("utf-8", "ignore")
        if any(v in t for v in vals) or pat.search(t):
            hits += 1; print("HIT", p)
print(f"   files {n}, env values checked {len(vals)}, hits {hits}")
sys.exit(1 if hits else 0)
PY
if [ "${1:-}" = "--mutation" ]; then echo "== mutation check"; python3 "$H/tests/mutation_l1.py" > "${TMPDIR:-/tmp}/l1-mutation.out" 2>&1; mrc=$?
  tail -1 "${TMPDIR:-/tmp}/l1-mutation.out"; grep -E "SURVIVED|BAD-MUTANT" "${TMPDIR:-/tmp}/l1-mutation.out"; [ $mrc -eq 0 ] || rc=1; fi
echo "L1 OFFLINE TESTS: $([ $rc -eq 0 ] && echo PASS || echo FAIL)"; exit $rc
