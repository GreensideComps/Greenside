#!/usr/bin/env bash
# Read-only full QA D1 snapshot (SW4C_SNAP form, snap.sql) to $1 (made absolute before the cd, so a relative path cannot land in the worktree). Exit non-zero if the query or parse fails.
D=__SCRATCHPAD__/b3stress; OUT=$(realpath -m "$1"); cd __SCRATCHPAD__/b3qa/greenside-entry-allocator || exit 1
npx wrangler d1 execute greenside_entries_qa --env qa --remote --json --command "$(cat $D/snap.sql)" 2>/dev/null | python3 -c "
import sys,json;t=sys.stdin.read();r=json.loads(t[t.index('['):]);row=r[0]['results'][0];assert row['marker']=='SW4C_SNAP';json.dump(row,open(sys.argv[1]+'.tmp','w'),separators=(',',':'))" "$OUT" && mv "$OUT.tmp" "$OUT"
