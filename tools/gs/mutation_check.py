#!/usr/bin/env python3
"""Mutation check for gs: applies each deliberate breakage to a COPY of tools/gs, runs test_gs.py against the copy, and requires the
tests to FAIL (mutant killed). A surviving mutant means a safety mechanism is not actually covered. Never touches the real files.
Usage: mutation_check.py     -> 'MUTATION CHECK: k/n mutants killed'; exit 0 iff all killed."""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
B3 = os.path.join(REPO, "qa", "b3-stress-harness")
# Mutants of the S4 fire script (fire.py), killed by b3stress-selftest-cmds/firetest.py
FIRE_MUTANTS = [
    # --- S4 transport repair: required breakages ---
    ("fire: connections opened before the long wait (pre-wait removed, workers open at once)", 'if fire_at_ms is not None:\n        wait_until(clock, fire_at_ms / 1000 - PRE_LEAD_S)', "pass"),
    ("fire: OLD ARCHITECTURE restored (open before the wait AND freshness gate removed)", [('if fire_at_ms is not None:\n        wait_until(clock, fire_at_ms / 1000 - PRE_LEAD_S)', "pass"), ("        if stale:\n            raise FireAborted", "        if False:\n            raise FireAborted")], None),
    ("fire: connection freshness gate removed", "        if stale:\n            raise FireAborted", "        if False:\n            raise FireAborted"),
    ("fire: stale connection accepted (age limit never reached)", '(now_ns - r["t_connected_ns"]) / 1e9 > MAX_CONN_AGE_S or', '(now_ns - r["t_connected_ns"]) / 1e9 > 1e9 * MAX_CONN_AGE_S or'),
    ("fire: idle-at-release limit removed", ' or (now_ns - r["t_probe_done_ns"]) / 1e9 > MAX_CONN_IDLE_S]', "]"),
    ("fire: failed preflight/probe ignored", "        if bad:\n            raise FireAborted", "        if False:\n            raise FireAborted"),
    ("fire: probe sent on a different connection than the mutation", 'st, h, raw = conns[i].request("POST", GRAPHQL_PATH, probe_bodies[i], hdr)', 'st, h, raw = tx.open().request("POST", GRAPHQL_PATH, probe_bodies[i], hdr)'),
    ("fire: failed (not-open) connection reused", "if not conns[i].is_open():", "if False:"),
    ("fire: failed probe connection reopened and re-probed", '            rec["probe_ok"], rec["probe_error"] = False, type(e).__name__', '            conns[i] = tx.open(); conns[i].request("POST", GRAPHQL_PATH, probe_bodies[i], hdr); rec["probe_ok"], rec["t_probe_done_ns"], rec["t_connected_ns"] = True, clock.mono_ns(), clock.mono_ns()'),
    ("fire: retry after a failed send", '            rec["result"], rec["error"] = "UNKNOWN", type(e).__name__\n            return', '            rec["result"], rec["error"] = "UNKNOWN", type(e).__name__\n            try:\n                c.send_phase("POST", GRAPHQL_PATH, action_bodies[i], hdr)()\n            except Exception:\n                pass\n            return'),
    ("fire: SSLEOF / send failure counted as delivery", '            rec["result"], rec["error"] = "UNKNOWN", type(e).__name__\n            return', '            rec["result"], rec["error"] = "UNKNOWN", type(e).__name__\n            rec["delivery_confirmed"] = True\n            return'),
    ("fire: attempts counted as concurrency evidence", 'conf = [(r["t_send_ns"], r["t_done_ns"]) for r in recs if r.get("delivery_confirmed") and "t_send_ns" in r and "t_done_ns" in r]', "conf = att"),
    ("fire: missing Shopify request id still counted as delivery", 'rec["delivery_confirmed"] = bool(rid)', 'rec["delivery_confirmed"] = True'),
    ("fire: distinct request ids not required for the verdict", '"concurrency_verdict": "NOT_PROVEN" if not (all_conf and len(set(ids)) == N)', '"concurrency_verdict": "NOT_PROVEN" if not (all_conf)'),
    ("fire: old-evidence evaluator counts attempts as deliveries", '"delivery_confirmed": r.get("http_status") is not None and bool(rid)', '"delivery_confirmed": True'),
    ("fire: one-shot marker check bypassed", "if os.path.exists(marker):", "if False:"),
    ("fire: one-shot marker no longer exclusive (overwritten)", "fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)", "fd = os.open(marker, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)"),
    ("fire: retired-fixture guard removed", 'if FIXTURE["competition_id"] in RETIRED_COMPETITIONS:', "if False:"),
    ("fire: QAI un-retired", 'RETIRED_COMPETITIONS = {"15905304379766":', 'RETIRED_COMPETITIONS = {} and {"15905304379766":'),
    ("fire: staggered (non-concurrent) sends", '        rec["t_release_ns"], rec["release_utc"] = clock.mono_ns(), iso(clock.wall_ns())\n', '        rec["t_release_ns"], rec["release_utc"] = clock.mono_ns(), iso(clock.wall_ns())\n        time.sleep(0.2 * i)\n'),
    ("fire: release-slip limit removed", "if clock.wall() > fire_at_ms / 1000 + MAX_SLIP_S:", "if False:"),
    ("fire: transport limits loosened beyond the observed proxy idle limit", "MAX_CONN_IDLE_S = 4.0", "MAX_CONN_IDLE_S = 15.0"),
    # --- earlier guards (still required) ---
    ("fire: target-id guard removed", "if draft_id not in TARGET_IDS:", "if False:"),
    ("fire: live gates ignored", 'if a.mode == "live" and gates:', "if False:"),
    ("fire: mutation selects order{} (inaccessible field)", 'draftOrder { id name status } "\n            "userErrors', 'draftOrder { id name status order { id } } "\n            "userErrors'),
    ("fire: production-Worker check dropped", 'if st != 404 and not (st == 200 and d.get("success") is False):', "if False:"),
    ("fire: confirmation phrase not required", "if a.confirm != CONFIRM_PHRASE:", "if False:"),
    ("fire: token scope check removed", 'if set(str(sess.scope or "").replace(",", " ").split()) != {REQUIRED_SCOPE}:', "if False:"),
    ("fire: draft status check removed", '("status", o.get("status") == "OPEN")', '("status", True)'),
    ("fire: draft quantity/title check removed", 'len(li) == 1 and li[0].get("quantity") == dr["qty"] and li[0].get("title") == FIXTURE["line_title"]', "True"),
    ("fire: guard-running gate removed", 'fails.append("guard.sh is not running")', "pass"),
    ("fire: arming-age gate removed", "if not 0 <= age <= ARMING_MAX_AGE_S:", "if False:"),
    ("fire: QA Worker DRY_RUN gate removed", 'if dry != ["false"]:', "if False:"),
    ("fire: fixture config check removed", "if cfg.get(k) != FIXTURE[k]:", "if False:"),
    ("fire: --fire-at window unchecked", "if a.fire_at is not None and not (-2000 <= a.fire_at - clock.wall() * 1000 <= MAX_FIRE_AHEAD_MS):", "if False:"),
    ("fire: credential leak check removed", "if any(s and s in blob for s in sess.secrets):", "if False:"),
]

MUTANTS = [
    ("SQL guard: PRAGMA no longer denied", '"PRAGMA", ', ""),
    ("SQL guard: DELETE no longer denied", '"DELETE", ', ""),
    ("SQL guard: second statement allowed", 'if ";" in s:\n        return False, "more than one statement"', "if False:\n        return False, 'x'"),
    ("SQL guard: first-keyword check removed", 'if not words or words[0].upper() not in ("SELECT", "WITH"):', "if not words:"),
    ("SQL guard: block comments not recognised", 'elif sql.startswith("/*", i):', 'elif False:'),
    ("SQL guard: line comments not recognised", 'if sql.startswith("--", i):', 'if False:'),
    ("SQL guard: quoted literals not stripped", 'out.append(" Q ")\n            i = j + 1\n        elif c == "["', 'out.append(sql[i:j + 1])\n            i = j + 1\n        elif c == "["'),
    ("SQL guard: literals parsed before comments (order bug)", 'if sql.startswith("--", i):', 'if c not in "\'\"`" and sql.startswith("--", i) and False:'),
    ("D1 client: validation skipped before the POST", '        ok, why = read_only_sql(sql)\n        if not ok:\n            raise ValueError("refused: " + why)\n        if not re.fullmatch', '        if not re.fullmatch'),
    ("D1 client: database id not validated", 'if not re.fullmatch(r"[0-9a-f-]{36}", uuid or ""):', "if False:"),
    ("GraphQL guard: mutation allowed", 'if "mutation" in ops or "subscription" in ops:', "if False:"),
    ("GraphQL guard: Shop.query skips the guard", '        ok, why = read_only_graphql(doc)\n        if not ok:\n            raise ValueError("refused: " + why)\n        c = self.tx.open()', "        c = self.tx.open()"),
    ("verify: production Worker check always passes", 'add("cf.production_worker_absent", cf["prod_worker_absent"] and c["prod_worker"] not in cf["workers"]', 'add("cf.production_worker_absent", True'),
    ("verify: DRY_RUN check inverted", 'cf["qa_dry_run"] == [c["expected_dry_run"]]', 'cf["qa_dry_run"] != [c["expected_dry_run"]]'),
    ("verify: single-version check removed", 'len(cf["qa_versions"]) == 1 and cf["qa_versions"][0][1] == 100', "True"),
    ("verify: cron check removed", 'cf["qa_crons"] == sorted(c["expected_crons"])', "True"),
    ("verify: integrity counters ignored", "add(\"d1.integrity_zero\", not bad,", 'add("d1.integrity_zero", True,'),
    ("verify: pool/capacity check removed", 'add("d1.pools_consistent", not odd,', 'add("d1.pools_consistent", True,'),
    ("verify: forbidden scope ignored", 'ok = sc == set(cfg["scopes_exact"]) and not (sc & set(cfg["scopes_forbidden"]))', 'ok = sc >= set(cfg["scopes_exact"])'),
    ("verify: stress-driver scope ceiling removed", 'ok = set(cfg["scopes_required"]) <= sc <= set(cfg["scopes_allowed"])', 'ok = set(cfg["scopes_required"]) <= sc'),
    ("verify: webhook callback URL ignored", 'topics == sorted(policy["webhooks"]["topics"]) and urls_ok', 'topics == sorted(policy["webhooks"]["topics"])'),
    ("verify: app identity ignored", 'add(f"shopify.{app}.identity", s["app"] == cfg["title"] and s["shop"] == policy["shop"]', 'add(f"shopify.{app}.identity", True'),
    ("verify: unexpected credentials ignored", 'add("env.no_unexpected_credentials", not unexpected,', 'add("env.no_unexpected_credentials", True,'),
    ("verify: PUT summary pool not counted", 'per[cid] = {"AVAILABLE": sm["available"] or 0, "OTHER": sm["n"] - (sm["available"] or 0)}', 'per[cid] = {}'),
    ("verdict: SKIP counts as pass", 'if any(r["status"] == "SKIP" for r in results) or errors:\n        return 2', "if False:\n        return 2"),
    ("verdict: FAIL does not fail", 'if any(r["status"] == "FAIL" for r in results):\n        return 1', "if False:\n        return 1"),
    ("audit log: records SQL text", 'extra = {"sql_chars": len(argv[2])} if len(argv) > 2 and argv[0] == "d1" else {}', 'extra = {"sql": argv[2]} if len(argv) > 2 and argv[0] == "d1" else {}'),
    ("audit log: never written", 'with open(path, "a") as f:', 'with open(os.devnull, "a") as f:'),
    ("claude-config audit: risky allow ignored", "if a in risky:", "if False:"),
    ("claude-config audit: missing deny rules ignored", 'if not any(out["deny_rules"].values()):', "if False:"),
    ("cli: d1 query skips the read-only check", '        ok, why = read_only_sql(args.sql)\n        if not ok:\n            P("REFUSED: " + why)\n            rc = 2\n        else:', "        if False:\n            pass\n        else:"),
]


def run_fire_mutant(name, old, new):
    tmp = tempfile.mkdtemp(prefix="firemut-")
    try:
        d = os.path.join(tmp, "b3stress")
        os.makedirs(d)
        s = open(os.path.join(B3, "b3stress", "fire.py")).read()
        for o, n in (old if isinstance(old, list) else [(old, new)]):     # a mutant may need several coordinated edits
            if o not in s:
                return None, f"anchor not found: {name}"
            s = s.replace(o, n, 1)
        open(os.path.join(d, "fire.py"), "w").write(s)
        try:
            r = subprocess.run([sys.executable, "-B", os.path.join(B3, "b3stress-selftest-cmds", "firetest.py"), d], capture_output=True, text=True, timeout=150)
        except subprocess.TimeoutExpired:
            return True, "test run hung (a guard that should refuse instead waited): counts as killed"
        fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
        return r.returncode != 0, (fails[0][:90] if fails else (r.stderr.strip().splitlines() or ["crash"])[-1][:90]) + f" ({len(fails)} failing)"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    killed, survived = 0, []
    for name, old, new in FIRE_MUTANTS:
        ok, info = run_fire_mutant(name, old, new)
        if ok is None:
            print("BROKEN MUTANT " + info); survived.append(name + " [anchor missing]")
        elif ok:
            killed += 1; print(f"KILLED   {name}  ({info})")
        else:
            survived.append(name); print(f"SURVIVED {name}")
    for name, old, new in MUTANTS:
        tmp = tempfile.mkdtemp(prefix="gsmut-")
        try:
            d = os.path.join(tmp, "tools", "gs")
            shutil.copytree(HERE, d, ignore=shutil.ignore_patterns("__pycache__"))
            os.symlink(os.path.join(REPO, "qa"), os.path.join(tmp, "qa"))
            subprocess.run(["git", "init", "-q", tmp], capture_output=True)
            p = os.path.join(d, "gs.py")
            s = open(p).read()
            if old not in s:
                print(f"BROKEN MUTANT (anchor not found, fix the mutation list): {name}")
                survived.append(name + " [anchor missing]")
                continue
            open(p, "w").write(s.replace(old, new, 1))
            try:
                r = subprocess.run([sys.executable, os.path.join(d, "test_gs.py"), d], capture_output=True, text=True, timeout=150)
            except subprocess.TimeoutExpired:
                killed += 1
                print(f"KILLED   {name}  (test run hung)")
                continue
            fails = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
            if r.returncode != 0:
                killed += 1
                print(f"KILLED   {name}  ({len(fails)} failing check(s), first: {fails[0][:90] if fails else r.stderr.strip().splitlines()[-1][:90]})")
            else:
                survived.append(name)
                print(f"SURVIVED {name}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    total = len(MUTANTS) + len(FIRE_MUTANTS)
    print(f"MUTATION CHECK: {killed}/{total} mutants killed" + ("" if not survived else "; SURVIVORS: " + "; ".join(survived)))
    return 0 if killed == total else 1


if __name__ == "__main__":
    sys.exit(main())
