#!/usr/bin/env python3
"""L1 mutation check: applies each deliberate breakage to a COPY of qa/l1-load-harness, runs the relevant test_l1 classes against
the copy, and requires them to FAIL (mutant killed). A surviving mutant means a safety mechanism is not actually covered.
Never touches the real files.  Usage: mutation_l1.py [-k SUBSTRING]  -> 'L1 MUTATION CHECK: k/n mutants killed'; exit 0 iff all."""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
L1H = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.abspath(os.path.join(L1H, "..", ".."))

G, F, LD, CM, CL, SH, ST, CA, RS, GU, GS, SS, RC, RQ, MF, LM = (
    "l1/governor.py", "l1/failwatch.py", "l1/load.py", "l1/common.py", "l1/clamp.py", "l1/shop.py", "l1/stage.py", "l1/canary.py",
    "l1/regsql.py", "l1/guardl.py", "l1/guardl.sh", "l1/shopsnapl.py", "l1/loadrecon.py", "l1/recon.sql", "l1/manifest.py",
    "l1/loadmetrics.py")
GOV, FW, DRV, CLA, SAM, STC, REC, REG, GRD, SNP, OBS, MAN, PLN = (
    "GovernorTests", "FailWatchTests", "DriverGateTests", "ClampTests", "SamplerTests", "StageCanaryTests", "ReconTests",
    "RegisterTests", "GuardTests", "ShopsnapTests", "ObservabilityTests", "ManifestRehearsalTests", "PlanTests")

# (description, file, old, new, test classes)
MUTANTS = [
    # governor
    ("governor: hard 5/s ceiling removed", G, "rate = min(rate, HARD_CEILING_PER_S)\n        assert 0.0 <= rate <= HARD_CEILING_PER_S", "rate = rate * 1.5", [GOV]),
    ("governor: WARNING in B ignored", G, '''            if why:
                self.step_log.append({"step": self.step, "rate": STAIRCASE[self.step], "result": "WARNING"''', '''            if False:
                self.step_log.append({"step": self.step, "rate": STAIRCASE[self.step], "result": "WARNING"''', [GOV]),
    ("governor: bucket < 600 LOAD STOP removed", G, "elif not in_t and last[1] < BUCKET_LOAD_STOP:", "elif False:", [GOV]),
    ("governor: >=3 THROTTLED LOAD STOP removed", G, "elif not in_t and len(recent_thr) >= THROTTLE_LOAD_STOP_N:", "elif False:", [GOV]),
    ("governor: unknown outcome ignored", G, "elif s.unknown_outcome:", "elif False:", [GOV]),
    ("governor: completion error ignored", G, "elif s.completion_error:", "elif False:", [GOV]),
    ("governor: stale bucket telemetry accepted", G, "elif last is None or now - last[0] > BUCKET_STALE_S:", "elif last is None:", [GOV]),
    ("governor: SAFETY ignored", G, 'if s.fw_level == "SAFETY_STOP" or s.safety:', "if False:", [GOV]),
    ("governor: restore not requested", G, 'actions = ["RESTORE_DRY_RUN"] if self.state == "SAFETY_STOP" and not self.restore_acked else []', "actions = []", [GOV]),
    ("governor: T on by default", G, "self.t_on, self.t2_on = approve_t == APPROVE_T, approve_t2 == APPROVE_T2", "self.t_on, self.t2_on = True, approve_t2 == APPROVE_T2", [GOV]),
    ("governor: T2 allowed without T", G, 'if approve_t2 and not approve_t:\n            raise Refused("T2 requires T to be approved as well")', "pass", [GOV]),
    ("governor: every step passes", G, "return sl >= SLOPE_PASS and mins >= BUCKET_WARN and not thr and done_ok", "return True", [GOV]),
    ("governor: cap-cut step promoted to r*", G, 'ok, result = None, "INCOMPLETE"', 'ok, result = True, "PASS"', [GOV]),
    ("governor: r* collapses on every tick in C", G, "if why and now - self.last_c_drop >= C_DROP_COOLDOWN_S:", "if why:", [GOV]),
    ("governor: LOAD STOP from failwatch ignored", G, 'if s.fw_level in ("LOAD_STOP",):', "if False:", [GOV]),
    # failwatch
    ("failwatch: consecutive rule removed", F, "if self.consecutive[topic] >= SAFETY_CONSECUTIVE:", "if False:", [FW]),
    ("failwatch: total rule removed", F, "if self.total[topic] >= SAFETY_TOTAL:", "if False:", [FW]),
    ("failwatch: repeated webhook-id rule removed", F, "if wid and self.fail_by_webhook[wid] >= 2:", "if False:", [FW]),
    ("failwatch: 5 s timeout not a failure", F, "return isinstance(w, (int, float)) and w > SHOPIFY_TIMEOUT_MS", "return False", [FW]),
    ("failwatch: tail/WL dedup removed", F, "        if k in self.seen:\n", "        if False:\n", [FW]),
    ("failwatch: unlimited T2 budget", F, "self.t2_budget -= 1", "pass", [FW]),
    ("failwatch: incomplete WL window accepted", F, "if not complete:", "if False:", [FW]),
    ("failwatch: failures after LOAD STOP not escalated", F, "if self.load_stop_at is not None and float(ev.get(\"ts\", 0)) > self.load_stop_at + FAIL_AFTER_LOAD_STOP_S:", "if False:", [FW]),
    # driver
    ("load: resend after a non-success", LD, "        outcome, rid = classify(st, h, body)\n", "        outcome, rid = classify(st, h, body)\n        if outcome != \"SUCCESS\":\n            st, h, body = client.post(MUTATION, {\"id\": row[\"draft_id\"]})\n            outcome, rid = classify(st, h, body)\n", [GOV]),
    ("load: marker not exclusive", LD, "fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)", "fd = os.open(marker, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)", [DRV]),
    ("load: marker check removed", LD, 'if os.path.exists(marker):\n        raise Refused(f"one-shot marker exists', 'if False:\n        raise Refused(f"one-shot marker exists', [DRV]),
    ("load: phrase check removed", LD, 'if confirm != CONFIRM_LIVE:\n        raise Refused("confirmation phrase missing or wrong")\n    probs', 'if False:\n        raise Refused("x")\n    probs', [DRV]),
    ("load: plan sha check removed", LD, "if plan_hash(plan) != expected_sha:", "if False:", [DRV]),
    ("load: exception after send not flagged unknown", LD, '            with lock:\n                flags["unknown"] = True\n            evidence.add("request", rec)\n            return', '            evidence.add("request", rec)\n            return', [GOV]),
    ("load: missing request id counted as delivery", LD, 'if not rid:\n        return "UNKNOWN", rid', "if False:\n        pass", [GOV, DRV]),
    ("load: driver keeps sending after a stop", LD, 'if d["state"] != "RUNNING" or d["phase"] in ("E", "DONE") or idx >= len(rows):', "if idx >= len(rows):", [GOV]),
    ("load: restore not called on SAFETY", LD, "ok = bool(restore_cb and restore_cb())", "ok = False", [GOV]),
    ("load: pacer burst after pause", LD, "self.acc += min(rate * dt, 1.0)", "self.acc += rate * dt", [GOV]),
    ("load: stale guard read as healthy", LD, 'if not g or now - float(g.get("ts", 0)) > self.guard_stale_s:', "if False:", [DRV]),
    # plan / common
    ("common: retired competition accepted", CM, 'if cid in RETIRED_COMPETITIONS:\n            p.append(f"competition {cid} is a retired fixture', 'if False:\n            p.append(f"competition {cid} is a retired fixture', [DRV]),
    ("common: quantity mix changed", CM, "((1, 300), (2, 225), (3, 150), (5, 60), (10, 15))", "((1, 301), (2, 225), (3, 150), (5, 60), (10, 14))", [PLN]),
    ("common: plan seed changed", CM, 'PLAN_SEED = "greenside-L1-QAL-2026-10"', 'PLAN_SEED = "greenside-L1-QAL-2026-11"', [PLN]),
    ("common: SAFETY consecutive threshold raised to Shopify's 8", CM, "SAFETY_CONSECUTIVE = 2", "SAFETY_CONSECUTIVE = 7", [FW]),
    # clamp
    ("clamp: T approval not required", CL, 'if approve_t != APPROVE_T:\n            raise Refused("clamp: T is not approved', 'if False:\n            raise Refused("clamp: T is not approved', [CLA]),
    ("clamp: phase check removed", CL, "if phase != kind:", "if False:", [CLA]),
    ("clamp: window maximum raised", CL, "max_s = CLAMP_MAX_WINDOW_S if kind == \"T\" else CLAMP_T2_MAX_WINDOW_S", "max_s = 10.0", [CLA]),
    ("clamp: own THROTTLED does not release", CL, "if is_throttled(body):\n                rec[\"release\"] = \"throttled (clamp's own request)\"\n                break", "if False:\n                pass", [CLA]),
    ("clamp: allocator THROTTLED does not release", CL, "if self.throttled_seen(t0):", "if False:", [CLA]),
    ("clamp: window budget removed", CL, "if self.windows[kind] >= limit:", "if False:", [CLA]),
    ("clamp: T2 approval not required", CL, 'if kind == "T2" and not self.t2_ok:', "if False:", [CLA]),
    # shop
    ("shop: read-only enforcement removed", SH, 'if self.cfg["read_only"]:\n            for d in self.documents:', "if False:\n            for d in self.documents:", [CLA]),
    ("shop: document allow-list bypassed", SH, "if document not in self.documents:", "if False:", [CLA]),
    # stage / canary
    ("stage: discount not 100%", ST, '"value": 100.0', '"value": 99.0', [STC]),
    ("stage: requiresShipping not checked", ST, '("tracked", True),\n                        ("requiresShipping", False)):', '("tracked", True)):', [STC]),
    ("stage: existing QAL drafts ignored", ST, 'if nodes:\n        raise Refused(f"stage: {len(nodes)}+', 'if False:\n        raise Refused(f"stage: {len(nodes)}+', [STC]),
    ("stage: continues after an error", ST, '        if not ok:\n            return {"mode": "execute", "status": "INCOMPLETE"', '        if False:\n            return {"mode": "execute", "status": "INCOMPLETE"', [STC]),
    ("stage: publication check removed", ST, 'if p.get("publications") != 0:', "if False:", [STC]),
    ("canary: registered product accepted", CA, "if cid in {str(c) for c in d1_competitions}:", "if False:", [STC]),
    ("canary: DRY_RUN gate removed", CA, 'if dry_run_value() != "true":', "if False:", [STC]),
    # registration
    ("regsql: chunk limit above D1's", RS, "MAX_BYTES = 90_000", "MAX_BYTES = 200_000", [REG]),
    ("regsql: retired competition accepted", RS, "if cid in RETIRED_COMPETITIONS:", "if False:", [DRV]),
    ("regsql: pool one short", RS, "for seq in range(START_NUMBER, START_NUMBER + CAPACITY):", "for seq in range(START_NUMBER, START_NUMBER + CAPACITY - 1):", [REG]),
    # guard
    ("guardl: subscription change not SAFETY", GU, 'if s.get("subs_ok") is False:', "if False:", [GRD]),
    ("guardl: non-L1 allocation not detected", GU, "if a[2] != cid:\n            dev.append", "if False:\n            dev.append", [GRD]),
    ("guardl: unexplained L1 allocation accepted", GU, 'dev.append(f"L1 allocation {a[4]} (qty {q}) not explained by a completed plan draft")', "pass", [GRD]),
    ("guardl: restore treated as verified early", GU, 'if w.get("restored_verified"):', "if True:", [GRD]),
    ("guardl: drain deadline removed", GU, 'elif drv.get("last_sent_ts") is not None and now - drv["last_sent_ts"] > DRAIN_DEADLINE_S:', "elif False:", [GRD]),
    ("guardl: no-progress stop removed", GU, "elif self.last_progress_t is not None and now - self.last_progress_t > NO_PROGRESS_S:", "elif False:", [GRD]),
    ("guardl: manual-stop ignored", GU, 'if inp.get("manual_stop"):', "if False:", [GRD]),
    ("guardl.sh: tick crash not fail-safe", GS, "  if [ $rc -ne 0 ]; then\n    now=$(date +%s)", "  if [ $rc -eq 10 ]; then\n    now=$(date +%s)", [GRD]),
    ("guardl.sh: failed restore not retried", GS, 'date -u +%FT%TZ >> "$D/RESTORE-ALARM"', 'exit 0; date -u +%FT%TZ >> "$D/RESTORE-ALARM"', [GRD]),
    # shopsnap
    ("shopsnapl: reads inside the load window", SS, 'return started and not g.get("restore_verified")', "return False", [SNP]),
    ("shopsnapl: stops after one page", SS, 'if not pi.get("hasNextPage"):\n            break', "break", [SNP]),
    # reconciliation
    ("loadrecon: quantity check removed", RC, "if not (q is not None and a[A_QTY] == q", "if False and (q is not None and a[A_QTY] == q", [REC]),
    ("loadrecon: range check removed", RC, 'ck("d1.allocated_exact_range", seqs == list(range(START_NUMBER, last + 1)),', 'ck("d1.allocated_exact_range", True,', [REC]),
    ("loadrecon: untouched entries not compared", RC, 'ck("untouched.entries", pe == qe,', 'ck("untouched.entries", True,', [REC]),
    ("loadrecon: duplicate issue not detected", RC, 'ck("events.one_per_issue", all(len(v) == 1 for v in issue.values()), "")', 'ck("events.one_per_issue", True, "")', [REC]),
    ("loadrecon: zero value not checked", RC, 'ck("shopify.zero_value", not nonzero,', 'ck("shopify.zero_value", True,', [REC]),
    ("loadrecon: allocation set not compared", RC, 'ck("d1.allocation_set_equals_orders", set(by_line) == set(exp_lines),', 'ck("d1.allocation_set_equals_orders", True,', [REC]),
    ("recon.sql: duplicate issue tolerated", RQ, "HAVING COUNT(*) > 1);\n-- check: ledger_drift_global", "HAVING COUNT(*) > 2);\n-- check: ledger_drift_global", [REC]),
    ("recon.sql: range check weakened", RQ, "((status = 'ALLOCATED' AND (seq < :start OR seq > :last)) OR (status = 'AVAILABLE' AND seq <= :last))", "((status = 'ALLOCATED' AND seq < :start) OR (status = 'AVAILABLE' AND seq < :start))", [REC]),
    ("recon.sql: zero value tolerated", RQ, "(unit_price_minor <> 0 OR line_total_minor <> 0)", "(unit_price_minor < 0)", [REC]),
    # evidence / metrics
    ("manifest: non-deterministic order", MF, "files.sort(key=lambda s: s.encode())", "files.sort(key=lambda s: s.lower())", [MAN]),
    ("loadmetrics: touching intervals overlap", LM, "key=lambda x: (x[0], x[1]))", "key=lambda x: (x[0], -x[1]))", [OBS]),
    ("loadmetrics: DERIVED reported as OBSERVED", LM, 'm["theoretical_capacity_orders_per_s"] = der(', 'm["theoretical_capacity_orders_per_s"] = obs(', [OBS]),
]


def run_one(desc, path, old, new, classes, base):
    work = tempfile.mkdtemp(prefix="l1mut-")
    try:
        dst = os.path.join(work, "qa", "l1-load-harness")
        shutil.copytree(L1H, dst, ignore=shutil.ignore_patterns("__pycache__"))
        os.symlink(os.path.join(REPO, "tools"), os.path.join(work, "tools"))
        os.symlink(os.path.join(REPO, "qa", "b3-stress-harness"), os.path.join(work, "qa", "b3-stress-harness"))
        f = os.path.join(dst, path)
        src = open(f).read()
        pairs = old if isinstance(old, list) else [(old, new)]
        for o, n in pairs:
            if src.count(o) != 1:
                return desc, "BAD-MUTANT", f"pattern found {src.count(o)} times"
            src = src.replace(o, n)
        open(f, "w").write(src)
        env = dict(os.environ, L1_GIT_REPO=REPO, PYTHONDONTWRITEBYTECODE="1")
        try:
            r = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest"] + [f"test_l1.{c}" for c in classes],
                               cwd=os.path.join(dst, "tests"), env=env, capture_output=True, text=True, timeout=600)
            killed = r.returncode != 0
        except subprocess.TimeoutExpired:
            killed = True
        return desc, "KILLED" if killed else "SURVIVED", ""
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv):
    sel = argv[argv.index("-k") + 1] if "-k" in argv else ""
    ms = [m for m in MUTANTS if sel in m[0]]
    every = [GOV, FW, DRV, CLA, SAM, STC, REC, REG, GRD, SNP, OBS, MAN, PLN]
    _, st, _ = run_one("baseline", "l1/common.py", 'PREFIX = "QAL"', 'PREFIX = "QAL"', every, None)
    if st != "SURVIVED":
        print("BASELINE FAILED: the unmutated copy does not pass its own tests; kills would be meaningless")
        return 2
    print("baseline: unmutated copy passes")
    res = [run_one(*m, base=None) for m in ms]
    for d, st, why in res:
        print(f"{st:10} {d} {why}")
    killed = sum(1 for _, st, _ in res if st == "KILLED")
    print(f"L1 MUTATION CHECK: {killed}/{len(res)} mutants killed")
    return 0 if killed == len(res) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
