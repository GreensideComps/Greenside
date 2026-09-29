#!/usr/bin/env bash
# Installs the B3 stress harness into a session scratchpad. Usage: qa/b3-stress-harness/install.sh <SCRATCHPAD_DIR>
# Copies b3stress, b3obs, b3stress-selftest-cmds, fixtures (-> b3live3) and b3stress-replay, and substitutes the scratchpad path.
# Holds NO credentials: all scripts read CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID / SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET from the environment.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"; DST="${1:?usage: install.sh <scratchpad dir>}"; mkdir -p "$DST"
for d in b3stress b3obs b3stress-selftest-cmds b3stress-replay; do rm -rf "$DST/$d"; cp -r "$SRC/$d" "$DST/$d"; done
rm -rf "$DST/b3live3"; cp -r "$SRC/fixtures/b3live3" "$DST/b3live3"
grep -rl "__SCRATCHPAD__" "$DST/b3stress" "$DST/b3obs" "$DST/b3stress-selftest-cmds" | xargs sed -i "s#__SCRATCHPAD__#$DST#g"
chmod +x "$DST"/b3stress/*.sh "$DST"/b3stress-selftest-cmds/*.sh "$DST"/b3obs/q.sh
echo "installed into $DST"
