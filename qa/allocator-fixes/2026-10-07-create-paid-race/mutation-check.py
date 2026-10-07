import os, shutil, subprocess, sys, tempfile
W = sys.argv[1]
P = "src/process.ts"
MUT = [
  ("catch every UNIQUE (no identity filter)", "      isLedgerIdentityConflict(err) &&\n", ""),
  ("no committed-row check", "      isLedgerIdentityConflict(err) &&\n      (await sameLedgerRowCommitted(deps, allocId, { competitionId, orderId, lineItemId }))\n", "      isLedgerIdentityConflict(err)\n"),
  ("no first-writer check (prelude)", "      prelude.length > 0 &&\n", ""),
  ("unbounded re-run", "      !raceRerun &&\n", ""),
  ("treat as success without re-run", "      return processLine(deps, order, line, reason, true);", "      return { ...base, competitionId, action: 'NONE', claimed: [], released: [], detail: 'race' };"),
  ("any error re-run (catch-all)", "    if (\n      !raceRerun &&\n      prelude.length > 0 &&\n      isLedgerIdentityConflict(err) &&\n      (await sameLedgerRowCommitted(deps, allocId, { competitionId, orderId, lineItemId }))\n    ) {", "    if (!raceRerun) {"),
  ("regex: identity may continue", "line_item_id)(?=$|:)/;", "line_item_id)/;"),
  ("regex: primary key alternative dropped", "(allocation\\.allocation_id|allocation\\.order_id, allocation\\.line_item_id)", "(allocation\\.order_id, allocation\\.line_item_id)"),
  ("regex: any table", "/UNIQUE constraint failed: (allocation", "/UNIQUE constraint failed: (\\w+"),
  ("committed row: shop_domain not compared", "    String(row['shop_domain']) === deps.shopDomain &&\n", ""),
  ("committed row: competition_id not compared", "    String(row['competition_id']) === ids.competitionId &&\n", ""),
  ("committed row: order_id not compared", "    String(row['order_id']) === ids.orderId &&\n", ""),
  ("committed row: line_item_id not compared", "\n    String(row['line_item_id']) === ids.lineItemId\n", "\n    true\n"),
  ("fix removed entirely (rethrow)", "    if (\n      !raceRerun &&", "    if (\n      false &&"),
]
killed = 0
for desc, old, new in MUT:
    work = tempfile.mkdtemp(prefix="allocmut-")
    dst = os.path.join(work, "a")
    shutil.copytree(W, dst, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    f = os.path.join(dst, P); s = open(f).read()
    if s.count(old) != 1:
        print(f"BAD-MUTANT {desc} ({s.count(old)})"); shutil.rmtree(work); continue
    open(f, "w").write(s.replace(old, new))
    r = subprocess.run(["npx", "vitest", "run", "tests/webhook-race.test.ts", "tests/atomicity.test.ts", "tests/reconcile.test.ts", "tests/webhook-handler.test.ts", "tests/process.test.ts"],
                       cwd=dst, capture_output=True, text=True, timeout=600)
    st = "KILLED" if r.returncode != 0 else "SURVIVED"
    killed += st == "KILLED"
    print(f"{st:9} {desc}")
    shutil.rmtree(work)
print(f"ALLOCATOR FIX MUTATION: {killed}/{len(MUT)} killed")
