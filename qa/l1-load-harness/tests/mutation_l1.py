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
LW, WS = "l1/livewin.py", "l1/window.sh"
SFW, SFL, SFS, SFR = "StageFourWorkerTests", "StageFourWorkersLogsTests", "StageFourStateFileTests", "StageFourRunbookTests"
GOV, FW, DRV, CLA, SAM, STC, REC, REG, GRD, SNP, OBS, MAN, PLN, AMD, CEX = (
    "GovernorTests", "FailWatchTests", "DriverGateTests", "ClampTests", "SamplerTests", "StageCanaryTests", "ReconTests",
    "RegisterTests", "GuardTests", "ShopsnapTests", "ObservabilityTests", "ManifestRehearsalTests", "PlanTests",
    "StaircaseAmendmentTests", "CanaryExclusionTests")

# (description, file, old, new, test classes)
MUTANTS = [
    # Stage 4 wiring: QA Worker input fails closed (guardl)
    ("wiring: missing Worker input not a stop", GU, "if now - (self.last_worker if self.last_worker is not None else self.t0) > WORKER_STALE_S:", "if False:", [SFW]),
    ("wiring: unverified production presence accepted", GU, 'if w.get("production_present") is not False:', 'if w.get("production_present") is True:', [SFW]),
    ("wiring: missing DRY_RUN accepted", GU, 'if w.get("dry_run") is None:\n                    self._safety("QA Worker DRY_RUN missing")', 'if False:\n                    self._safety("QA Worker DRY_RUN missing")', [SFW]),
    ("wiring: unexpected DRY_RUN value accepted", GU, 'elif w.get("dry_run") != "false":', 'elif w.get("dry_run") not in ("false", "true", "False", "FALSE", "", "0", "<multiple>"):', [SFW]),
    ("wiring: gate-passed version not checked", GU, 'if not exp or w.get("versions") != [[exp, 100]]:', "if not exp:", [SFW]),
    ("wiring: failed reading judged as good", GU, 'if w.get("read_ok") is True and isinstance(ts, (int, float)) and now - ts <= WORKER_STALE_S:', "if isinstance(ts, (int, float)):", [SFW]),
    # Stage 4 wiring: Workers Logs input fails closed (guardl)
    ("wiring: Workers Logs staleness removed", GU, "elif now * 1000 - self.wl_hi_ms > WL_STALE_S * 1000:", "elif False:", [SFL]),
    ("wiring: Workers Logs gap accepted", GU, "elif self.wl_hi_ms is None or lo > self.wl_hi_ms + 1:", "elif self.wl_hi_ms is None:", [SFL]),
    ("wiring: Workers Logs windows not fed to failwatch", GU, 'self.fw.confirm_wl(win.get("events") or [], bool(win.get("complete")))', "pass", [SFL]),
    ("wiring: unknown coverage start accepted", GU, 'self._load("Workers Logs coverage start unknown (wl_from_ms missing)")', "pass", [SFL]),
    ("wiring: malformed window accepted", GU, 'self._load("Workers Logs window malformed (no valid from_ms/to_ms)")', "pass", [SFL]),
    ("wiring: corrupt poller line dropped", GU, 'out.append({"complete": False, "events": [], "problems": ["unparseable wl-poll line"]})', "pass", [SFL]),
    ("wiring: collect ignores the Worker poller", GU, 'inp["worker"] = w', "pass", [SFL]),
    # Stage 4 wiring: livewin.py readings and state files
    ("livewin: production rule loosened", LW, 'if st == 404 or (st == 200 and d.get("success") is False):', 'if st != 200 or d.get("success") is False:', [SFW]),
    ("livewin: unusable GET read as ok", LW, 'r["read_ok"] = r["production_present"] is not None and settings_ok and r["versions"] is not None', 'r["read_ok"] = True', [SFW]),
    ("livewin: transport error read as ok", LW, 'r["problems"].append(f"transport error: {type(e).__name__}")\n        r["read_ok"] = False', 'r["problems"].append(f"transport error: {type(e).__name__}")\n        r["read_ok"] = True', [SFW]),
    ("livewin: several DRY_RUN bindings read as the first", LW, 'r["dry_run"] = dry[0] if len(dry) == 1 else (None if not dry else "<multiple>")', 'r["dry_run"] = dry[0] if dry else None', [SFW]),
    ("livewin: incomplete Workers Logs window advances coverage", LW, 'if w["complete"]:\n            self.next = hi + 1\n        return w', "self.next = hi + 1\n        return w", [SFL]),
    ("livewin: non-OPEN draft counted as open", LW, 'elif n.get("status") != "OPEN":', "elif False:", [SFS]),
    ("livewin: D1 reference not checked", LW, "p = registration_problems(snap, cid) + allowlist(snap, snap, cid, [], pre_max)", "p = []", [SFS]),
    ("livewin: stale drafts-open accepted", LW, 'if not 0 <= now - float(dr.get("ts", 0)) <= PRECHECK_MAX_AGE_S:', "if False:", [SFS]),
    ("livewin: guard reasons ignored at arming", LW, 'if g.get("safety") or g.get("load") or g.get("actions"):', "if False:", [SFS]),
    ("livewin: restore verified without the restore gate", LW, 'if _txt(_p(d, "restoregate.txt")) != "PASSED" or not rv:', "if not rv:", [SFW]),
    # Stage 4 wiring: window.sh sequencing
    ("window.sh: live without the phrase", WS, 'if [ "$CMD" = live ] && [ "${5:-}" != "$PHRASE_LIVE" ]; then', "if false; then", [SFR]),
    ("window.sh: restore not serialised", WS, "flock -w 1800 9 ||", "true ||", [SFR]),
    ("window.sh: verified restore deployed again", WS, 'if [ "$($LW verify-restore --state-dir "$D")" = true ]; then echo "restore already verified (no second deploy)"; exit 0; fi', ":", [SFR]),
    ("window.sh: gate failure not stopping", WS, '[ "$(cat "$D/gate.txt" 2>/dev/null)" = PASSED ] || abort gate', "true || abort gate", [SFR]),
    ("window.sh: armed state check skipped", WS, '$LW check-state --state-dir "$D" --plan "$PLAN" --plan-sha "$SHA" --phase armed || stop_after_gate armed "armed state check"', "true", [SFR]),
    ("window.sh: driver refusal not restoring", WS, '[ "$LRC" = 2 ] && { log "load.py refused before any mutation -> manual-stop"; touch "$D/manual-stop"; }', "true", [SFR]),
    ("window.sh: no restore when the guard is gone", WS, 'log "no running guard: strict restore directly"; bash "$H/window.sh" restore "$S"', ":", [SFR]),
    ("window.sh: Workers Logs poller left running after a failed gate", WS, 'kill "$(cat "$D/poll-wl.pid")" 2>/dev/null; abort gate', "abort gate", [SFR]),
    ("window.sh: pre-live checks skipped before the gate", WS, 'prelive\nif [ "$CMD" = prelive ]', 'if [ "$CMD" = prelive ]', [SFR]),
    # known-canary exclusion (Stage 2 follow-up)
    ("canex: canary excluded by tag instead of exact id", CM, [('pop = [o for o in orders if not is_known_canary_order(o.get("id"))]', 'pop = [o for o in orders if "QAL-CANARY" not in (o.get("tags") or [])]'), ('return pop, [o for o in orders if is_known_canary_order(o.get("id"))]', 'return pop, [o for o in orders if "QAL-CANARY" in (o.get("tags") or [])]')], None, [CEX]),
    ("canex: canary order exclusion removed", CM, 'pop = [o for o in orders if not is_known_canary_order(o.get("id"))]', "pop = list(orders)", [CEX]),
    ("canex: canary matched by id prefix", CM, 'return gid == KNOWN_CANARY["order_gid"]', 'return str(gid).startswith("gid://shopify/Order/135992606")', [CEX]),
    ("canex: staging excludes any canary-tagged draft", ST, "return [n for gid, n in seen.items() if not is_known_canary_draft(gid)]", 'return [n for gid, n in seen.items() if "QAL-CANARY" not in (n.get("tags") or [])]', [CEX]),
    ("canex: staging ignores qa-load drafts", ST, "for n in lists[0] + lists[1]:", "for n in lists[0]:", [CEX]),
    ("canex: failed draft listing read as empty", ST, 'if status != 200 or any(x is None for x in lists) or (body or {}).get("errors"):', "if False:", [CEX]),
    ("canex: unexpected drafts tolerated in reconciliation", RC, 'ck("shopify.no_unexpected_drafts", not extra,', 'ck("shopify.no_unexpected_drafts", True,', [CEX]),
    ("canex: canary allocation tolerated", RC, 'ck("d1.known_canary_has_no_allocation", all(', 'ck("d1.known_canary_has_no_allocation", True or all(', [CEX]),
    ("canex: second canary allowed", CA, 'if KNOWN_CANARY.get("order_gid"):', "if False:", [CEX, STC]),
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
    # staircase amendment (6 Oct 2026). The old "cap-cut step promoted" mutant was removed: with the cap equal to the sum of the
    # step quotas that defensive branch is unreachable, so a mutant there cannot be killed by any behaviour.
    ("amend: step ends on time (40 s) instead of its 30 s quota", G, 'elif self.sent["B"] - self.step_base >= STEP_QUOTAS[self.step]:', "elif now - self.step_start >= 40.0:", [AMD]),
    ("amend: staircase extended to 4.5 and 5.0/s", CM, [("STAIRCASE = (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0)", "STAIRCASE = (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)"), ("assert STAIRCASE[-1] <= HARD_CEILING_PER_S and PHASE_B_MAX_ORDERS == 525", "assert True")], None, [AMD]),
    ("amend: T approved without a budget", G, "if self.t_on and t_budget != PHASE_T_ORDERS:", "if False:", [AMD]),
    ("amend: T budget silently taken from nowhere (C not reduced)", G, "self.reserve_t = t_budget", "self.reserve_t = 0", [AMD]),
    ("amend: population check removed", CM, "if n > N_ORDERS or c_min < 0:", "if False:", [AMD]),
    ("amend: unbracketed result worded as a ceiling", CM, 'return (f"Observed sustainable throughput >= {lo} orders/s; upper stability boundary not bracketed by L1 "', 'return (f"Allocator ceiling is {lo} orders/s "', [AMD]),
    ("amend: lower bound not flagged in metrics", LM, 'lower_bound_only = bool(br) and not br.get("bracketed")', "lower_bound_only = False", [AMD]),
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
    ("stage: existing QAL drafts ignored", ST, 'if nodes:\n        raise Refused(f"stage: unexpected QAL/qa-load draft(s)', 'if False:\n        raise Refused(f"stage: unexpected QAL/qa-load draft(s)', [STC, CEX]),
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
    every = [GOV, FW, DRV, CLA, SAM, STC, REC, REG, GRD, SNP, OBS, MAN, PLN, AMD, CEX, SFW, SFL, SFS, SFR]
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
