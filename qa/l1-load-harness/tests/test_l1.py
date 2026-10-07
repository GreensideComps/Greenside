#!/usr/bin/env python3
"""L1 offline tests (Stage 1). No network, no Shopify, no D1, no Worker: every external is a fake or a local SQLite database built
from the allocator's own migrations (git show e917bb5). Test names carry the requirement number (R01..R38) they prove.
Run: python3 -m unittest -v test_l1   (from this directory)  or  ../run-tests.sh"""
import contextlib, hashlib, io, json, os, random, shutil, sqlite3, subprocess, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
L1 = os.path.abspath(os.path.join(HERE, "..", "l1"))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
GIT_REPO = os.environ.get("L1_GIT_REPO", REPO)     # mutation copies run outside the git checkout
sys.path.insert(0, L1)

import common  # noqa: E402
from common import (APPROVE_T, APPROVE_T2, CONFIRM_CANARY, CONFIRM_LIVE, CONFIRM_REHEARSE, CONFIRM_STAGE, Refused,  # noqa: E402
                    plan_hash, plan_template, validate_plan)
import failwatch, governor, guardl, load, loadmetrics, loadrecon, manifest, regsql, sampler, shopsnapl, stage, canary  # noqa: E402,E401
import clamp as clampmod, rehearse, shop  # noqa: E402,E401
from governor import Governor, Signals  # noqa: E402
from sim import FakeClock, SimClient, World  # noqa: E402

ALLOC_COMMIT = "e917bb5"
L1_CID = "999000111"
PINNED_PLAN_SHA = "a0f6f0117e756db0540eb76d07ba626be7ec2adde786c7b628e4727fb0c34b04"   # sha256 of the canonical 750-row template; any change to the plan is a reviewed change


def bound_plan(cid=L1_CID):
    p = plan_template()
    p.update(kind="L1-plan-bound", competition_id=cid, product_gid=f"gid://shopify/Product/{cid}",
             variant_gid="gid://shopify/ProductVariant/1")
    for r in p["rows"]:
        r["draft_id"] = f"gid://shopify/DraftOrder/{900000 + r['index']}"
        r["draft_name"] = f"#D{1000 + r['index']}"
    return p


def tmpdir():
    d = tempfile.mkdtemp(prefix="l1test-")
    return d


# ---- fakes ----------------------------------------------------------------------------------------------------------------------
class FakeConn:
    def __init__(self, tx):
        self.tx = tx

    def request(self, method, path, body, headers):
        return self.tx.handle(method, path, body, headers)

    def close(self):
        pass


class FakeTx:
    """Fake fixed-host transport: token exchange + a GraphQL handler. Records every body sent."""

    def __init__(self, handler, scope="read_orders,read_products"):
        self.handler, self.scope, self.sent = handler, scope, []

    def open(self):
        return FakeConn(self)

    def handle(self, method, path, body, headers):
        if path == shop.TOKEN_PATH:
            return 200, {}, json.dumps({"access_token": "tok-SECRET-123456", "scope": self.scope}).encode()
        self.sent.append(json.loads(body))
        st, h, b = self.handler(json.loads(body))
        return st, h, json.dumps(b).encode()


ENV = {"SHOPIFY_CLIENT_ID": "cid-SECRET-aaaaaa", "SHOPIFY_CLIENT_SECRET": "sec-SECRET-bbbbbb",
       "QA_STRESS_SHOPIFY_CLIENT_ID": "qcid-SECRET-cccc", "QA_STRESS_SHOPIFY_CLIENT_SECRET": "qsec-SECRET-dddd"}


def cost(av, throttled=False):
    b = {"extensions": {"cost": {"requestedQueryCost": 1, "actualQueryCost": 1,
                                 "throttleStatus": {"maximumAvailable": 2000.0, "currentlyAvailable": av, "restoreRate": 100.0}}}}
    if throttled:
        b["errors"] = [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}]
    else:
        b["data"] = {"shop": {"id": "gid://shopify/Shop/1"}}
    return b


def sim_run(cost_pts=28.0, gov=None, client_fail=None, extra=None, plan=None):
    clock = FakeClock()
    world = World(clock, cost=cost_pts)
    plan = plan or bound_plan()
    gov = gov or Governor()
    d = tmpdir()
    marker = os.path.join(d, "load-x.done")
    ev = load.Evidence(None)
    client = SimClient(world, client_fail)
    decisions = []

    def sig(now, arr):
        s = world.signals(now, arr)
        if extra:
            extra(now, s, world)
        return s
    restores = []
    summ = load.run(plan, client, gov, sig, clock, marker, ev, restore_cb=lambda: restores.append(clock.now()) or True)
    t_end = clock.now() + 3600
    while not world.signals(clock.now()).drained and clock.now() < t_end:
        clock.sleep(1)
    shutil.rmtree(d)
    return {"summary": summ, "world": world, "client": client, "gov": gov, "evidence": ev, "restores": restores, "clock": clock}


# =================================================================================================================================
class PlanTests(unittest.TestCase):
    def test_R01_plan_deterministic(self):
        a, b = plan_template(), plan_template()
        self.assertEqual(a, b)
        self.assertEqual(plan_hash(a), plan_hash(b))
        self.assertEqual(len(a["rows"]), 750)
        self.assertEqual([r["index"] for r in a["rows"]], list(range(1, 751)))
        # independent re-derivation of the seeded order (sha256 sort), not using common.plan_rows
        qtys = [q for q, n in ((1, 300), (2, 225), (3, 150), (5, 60), (10, 15)) for _ in range(n)]
        order = sorted(range(750), key=lambda k: hashlib.sha256(f"greenside-L1-QAL-2026-10:{k}".encode()).hexdigest())
        self.assertEqual([r["qty"] for r in a["rows"]], [qtys[k] for k in order])
        self.assertEqual(validate_plan(a), [])

    def test_R01b_plan_hash_pinned_across_runs(self):
        # the canonical hash is stable within and across interpreter runs
        out = subprocess.run([sys.executable, "-c", f"import sys;sys.path.insert(0,{L1!r});import common;"
                              "print(common.plan_hash(common.plan_template()))"], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), plan_hash(plan_template()))
        self.assertEqual(plan_hash(plan_template()), PINNED_PLAN_SHA)

    def test_R02_quantity_mix(self):
        rows = plan_template()["rows"]
        mix = {}
        for r in rows:
            mix[r["qty"]] = mix.get(r["qty"], 0) + 1
        self.assertEqual(mix, {1: 300, 2: 225, 3: 150, 5: 60, 10: 15})
        self.assertEqual(sum(r["qty"] for r in rows), 1650)
        self.assertEqual(common.UNITS, 1650)
        self.assertEqual(common.CAPACITY - common.UNITS, 350)
        for r in rows:
            self.assertEqual(common.parse_tag([common.ORDER_TAG, "QAL", r["tag"]]), (r["index"], r["qty"]))

    def test_plan_validation_detects_tampering(self):
        p = plan_template()
        p["rows"][0]["qty"] += 1
        self.assertTrue(validate_plan(p))
        q = bound_plan()
        q["rows"][5]["draft_id"] = q["rows"][4]["draft_id"]
        self.assertIn("duplicate draft GIDs", validate_plan(q, bound=True))


# =================================================================================================================================
class GovernorTests(unittest.TestCase):
    def test_R03_staircase_transitions(self):
        r = sim_run(36.0)            # model capacity 100/36 = 2.78 orders/s
        g = r["gov"].summary()
        self.assertEqual([(s["rate"], s["result"]) for s in g["steps"]],
                         [(1.0, "PASS"), (1.5, "PASS"), (2.0, "PASS"), (2.5, "PASS"), (3.0, "FAIL")])
        self.assertEqual(g["stability_bracket"]["low"], 2.5)
        self.assertEqual(g["stability_bracket"]["high"], 3.0)
        self.assertTrue(g["stability_bracket"]["bracketed"])
        phases = [x["event"] for x in g["log"] if x["event"].startswith("phase")]
        self.assertEqual(phases[0], "phase A->B")
        self.assertIn("phase B->C", phases)
        self.assertEqual(phases[-1], "phase C->E")
        self.assertNotIn("T", "".join(p.split("->")[1] for p in phases))      # default path never enters T/T2
        self.assertEqual(g["sent"]["T"] + g["sent"]["T2"], 0)
        self.assertEqual(r["summary"]["dispatched"], 750)

    def test_R03b_default_path_is_A_B_C_E(self):
        r = sim_run(28.0)            # model capacity 3.57 orders/s
        seq = ["A"] + [x["event"].split("->")[1] for x in r["gov"].log if x["event"].startswith("phase")]
        self.assertEqual(seq, ["A", "B", "C", "E"])
        g = r["gov"].summary()
        self.assertEqual([(s["rate"], s["result"]) for s in g["steps"]][-2:], [(3.5, "PASS"), (4.0, "FAIL")])
        self.assertEqual(g["r_star"], 3.5)

    def test_R03c_persisting_warning_in_C_does_not_collapse_r_star(self):
        r = sim_run(48.0)            # model capacity 2.08/s
        g = r["gov"].summary()
        self.assertEqual((g["stability_bracket"]["low"], g["stability_bracket"]["high"]), (2.0, 2.5))
        self.assertGreaterEqual(g["r_star"], 1.5, "r* collapsed on every tick of a persisting WARNING")

    def test_R04_warning_drops_immediately_to_r_star(self):
        g = Governor()
        now = 1000.0
        full = [(now - 20 + i * 2, 2000) for i in range(11)]
        g.sent["A"] = 50
        d = g.update(now, Signals(bucket=full))
        self.assertEqual((d["phase"], d["rate"]), ("B", 1.0))
        # pass 1.0 and 1.5 (a step completes when its quota, rate x 30 s, has been sent)
        for k in range(2):
            now += 30
            g.sent["B"] += common.STEP_QUOTAS[k]
            d = g.update(now, Signals(bucket=[(now - 30 + i * 2, 2000) for i in range(16)]))
        self.assertEqual((d["phase"], d["rate"]), ("B", 2.0))
        # one tick with a WARNING (bucket < 1200): the very same decision is already C at the last passing rate
        now += 3
        d = g.update(now, Signals(bucket=[(now - 2, 1600), (now, 1150)]))
        self.assertEqual((d["phase"], d["rate"], d["r_star"]), ("C", 1.5, 1.5))
        self.assertEqual(g.summary()["steps"][-1]["result"], "WARNING")

    def test_R04b_throttled_and_wall_are_warnings(self):
        for sig in (dict(throttled=[999.0]), dict(wall_ms_max=3500)):
            g = Governor()
            g.sent["A"] = 50
            g.update(990.0, Signals(bucket=[(989.0, 2000)]))
            d = g.update(1000.0, Signals(bucket=[(999.0, 2000)], **sig))
            self.assertEqual(d["phase"], "C", sig)

    def test_R05_never_exceeds_ceiling(self):
        r = sim_run(1.0)             # essentially free allocator: every step passes
        rates = [d["rate"] for d in r["evidence"].decisions if "rate" in d]
        self.assertTrue(rates)
        self.assertLessEqual(max(rates), 5.0)
        # fuzz: random signals never yield a rate above the ceiling
        rnd = random.Random(7)
        for _ in range(200):
            g = Governor()
            t = 0.0
            for _ in range(300):
                t += rnd.choice((0.1, 1, 5, 41))
                g.sent[g.phase if g.phase in g.sent else "C"] += rnd.choice((0, 1, 10))
                d = g.update(t, Signals(bucket=[(t - 1, rnd.choice((2000, 1990, 1500, 900)))], throttled=[]))
                self.assertLessEqual(d["rate"], 5.0)
        self.assertEqual(common.HARD_CEILING_PER_S, 5.0)

    def test_R06_never_deliberately_empties_bucket(self):
        for c in (10, 20, 28, 36, 48, 60, 80, 120, 200):
            r = sim_run(float(c))
            w = r["world"]
            self.assertGreater(w.min_bucket, common.BUCKET_LOAD_STOP, f"cost {c}: bucket fell to {w.min_bucket}")
            self.assertEqual(len(w.throttled), 0, f"cost {c}: THROTTLED in the default path")
            self.assertEqual(w.fw.level, "NONE", f"cost {c}")
            self.assertEqual(len(w.completed), 750, f"cost {c}")
        # an allocator slower than even the 0.5/s baseline: the run LOAD-STOPs before the bucket empties, nothing is throttled
        for c in (300.0, 600.0):
            r = sim_run(c)
            w = r["world"]
            self.assertEqual(r["gov"].state, "LOAD_STOP", c)
            self.assertGreater(w.min_bucket, 0, c)
            self.assertEqual(len(w.throttled), 0, c)
            self.assertEqual(len(w.completed), len(w.created), c)

    def test_R07_load_stop_stops_arrivals_window_drains(self):
        fired = {}

        def extra(now, s, world):
            if len(world.created) >= 200:
                s.load = ["test LOAD STOP"]
                fired.setdefault("t", now)
        r = sim_run(28.0, extra=extra)
        g = r["gov"]
        self.assertEqual(g.state, "LOAD_STOP")
        self.assertIn(g.phase, ("E", "DONE"))
        sent_after = [x for x in r["evidence"].records if x["t_send"] > fired["t"] + 0.2]
        self.assertEqual(sent_after, [], "a request was sent after the LOAD STOP")
        self.assertLess(r["summary"]["dispatched"], 750)
        self.assertEqual(len(r["world"].completed), len(r["world"].created), "dispatched orders did not drain")
        self.assertEqual(r["restores"], [], "LOAD STOP must not restore")
        self.assertTrue(r["summary"]["never_sent"])
        # the driver loop itself ends at the stop (it does not idle on until its time limit)
        self.assertFalse(any("time limit" in str(d.get("event")) for d in r["evidence"].decisions))
        summ = [d for d in r["evidence"].decisions if d.get("event") == "summary"][0]
        self.assertLess(summ["t"] - fired["t"], 5.0, "driver kept looping after the LOAD STOP")

    def test_R12b_failwatch_load_stop_reaches_governor(self):
        g = Governor()
        d = g.update(1.0, Signals(bucket=[(1.0, 2000)], fw_level="LOAD_STOP"))
        self.assertEqual((d["state"], d["rate"], d["actions"]), ("LOAD_STOP", 0.0, []))
        self.assertIn("failwatch LOAD_STOP", g.log[-1]["why"])
        g = Governor()
        self.assertEqual(g.update(1.0, Signals(bucket=[(1.0, 2000)], fw_level="WARNING"))["state"], "RUNNING")

    def test_R08_safety_stop_enters_restore(self):
        def extra(now, s, world):
            if len(world.created) >= 100:
                s.safety = ["test SAFETY"]
        r = sim_run(28.0, extra=extra)
        self.assertEqual(r["gov"].state, "SAFETY_STOP")
        self.assertEqual(len(r["restores"]), 1)
        self.assertTrue(any(d.get("event") == "RESTORE_DRY_RUN requested" and d.get("ok") for d in r["evidence"].decisions))
        g = Governor()
        d = g.update(1.0, Signals(bucket=[(1.0, 2000)], fw_level="SAFETY_STOP"))
        self.assertEqual((d["state"], d["rate"], d["actions"]), ("SAFETY_STOP", 0.0, ["RESTORE_DRY_RUN"]))
        d = g.update(2.0, Signals(bucket=[(2.0, 2000)]))
        self.assertEqual(d["actions"], ["RESTORE_DRY_RUN"], "restore must be requested on every tick until acknowledged")
        g.ack_restore()
        self.assertEqual(g.update(3.0, Signals(bucket=[(3.0, 2000)]))["actions"], [])
        # SAFETY outranks LOAD_STOP and DONE
        g2 = Governor()
        g2.update(1.0, Signals(bucket=[(1.0, 2000)], load=["x"]))
        self.assertEqual(g2.update(2.0, Signals(bucket=[(2.0, 2000)], safety=["y"]))["state"], "SAFETY_STOP")

    def test_R13_unknown_outcome_load_stop(self):
        for mode in ("raise", "norid"):
            p = bound_plan()
            bad = p["rows"][60]["draft_id"]
            r = sim_run(28.0, client_fail={bad: mode}, plan=p)
            self.assertEqual(r["gov"].state, "LOAD_STOP", mode)
            self.assertTrue(any(x["outcome"] == "UNKNOWN" for x in r["evidence"].records))
            self.assertTrue(any("unknown outcome" in x["why"] for x in r["gov"].log if "why" in x))

    def test_R13b_completion_error_load_stop(self):
        p = bound_plan()
        r = sim_run(28.0, client_fail={p["rows"][70]["draft_id"]: "usererror"}, plan=p)
        self.assertEqual(r["gov"].state, "LOAD_STOP")

    def test_R14_bucket_below_600_load_stop(self):
        g = Governor()
        d = g.update(1.0, Signals(bucket=[(1.0, 599)]))
        self.assertEqual(d["state"], "LOAD_STOP")
        g = Governor()
        self.assertEqual(g.update(1.0, Signals(bucket=[(1.0, 600)]))["state"], "RUNNING")
        g = Governor()       # stale telemetry is never read as healthy
        self.assertEqual(g.update(100.0, Signals(bucket=[(80.0, 2000)]))["state"], "LOAD_STOP")
        self.assertEqual(Governor().update(1.0, Signals(bucket=[]))["state"], "LOAD_STOP")

    def test_R15_three_throttled_in_10s_load_stop(self):
        g = Governor()
        self.assertEqual(g.update(10.0, Signals(bucket=[(10.0, 2000)], throttled=[-1.0, 9.0, 10.0]))["state"], "RUNNING")
        g = Governor()
        self.assertEqual(g.update(10.0, Signals(bucket=[(10.0, 2000)], throttled=[1.0, 5.0, 9.0]))["state"], "LOAD_STOP")

    def test_R16_no_retry_or_resend(self):
        p = bound_plan()
        fail = {p["rows"][i]["draft_id"]: m for i, m in ((10, "throttled"), (20, "throttled"), (30, "usererror"))}
        r = sim_run(28.0, client_fail=fail, plan=p)
        calls = r["client"].calls
        self.assertEqual(len(calls), len(set(calls)), "a draft was sent twice")
        self.assertEqual([x["draft_id"] for x in r["evidence"].records], calls)
        thr = [x for x in r["evidence"].records if x["outcome"] == "THROTTLED_NOT_EXECUTED"]
        self.assertEqual(len(thr), 2)
        # also with every outcome failing, each draft is still posted at most once
        r2 = sim_run(28.0, client_fail={x["draft_id"]: "throttled" for x in p["rows"]}, plan=p)
        self.assertEqual(len(r2["client"].calls), len(set(r2["client"].calls)))

    def test_R16b_pacer_no_catchup_burst(self):
        pc = load.Pacer()
        self.assertEqual(pc.tokens(0.0, 5.0), 0)
        self.assertEqual(pc.tokens(100.0, 5.0), 1, "a long pause must not release a burst")
        n = sum(pc.tokens(100.0 + i * 0.1, 2.5) for i in range(1, 401))
        self.assertIn(n, (99, 100, 101))

    def test_R21_R22_T_and_T2_need_approval(self):
        with self.assertRaises(Refused):
            Governor(approve_t="yes")
        with self.assertRaises(Refused):
            Governor(approve_t2=APPROVE_T2)                  # T2 without T
        with self.assertRaises(Refused):
            Governor(approve_t=APPROVE_T, approve_t2="x")
        g = Governor()
        self.assertFalse(g.t_on or g.t2_on)
        self.assertEqual(g.reserve_t, 0)
        g = Governor(approve_t=APPROVE_T, t_budget=40)
        self.assertTrue(g.t_on and not g.t2_on)
        self.assertEqual(g.reserve_t, 40)
        r = sim_run(28.0, gov=Governor(approve_t=APPROVE_T, t_budget=40))
        self.assertEqual(r["gov"].sent["T"], 40)
        self.assertEqual(r["gov"].sent["T2"], 0)
        r = sim_run(28.0, gov=Governor(approve_t=APPROVE_T, approve_t2=APPROVE_T2, t_budget=40))
        self.assertEqual((r["gov"].sent["T"], r["gov"].sent["T2"]), (20, 20))


# =================================================================================================================================
class StaircaseAmendmentTests(unittest.TestCase):
    """Amendment of 6 Oct 2026: 30 s steps at 1.0..4.0/s, escalation stops after a judged 4.0/s step, explicit T budget."""

    def test_A01_every_default_step_is_30_seconds(self):
        self.assertEqual(common.STEP_SECONDS, 30.0)
        self.assertEqual(common.STEP_QUOTAS, tuple(int(r * 30) for r in common.STAIRCASE))
        r = sim_run(10.0)            # every step passes
        steps = r["gov"].summary()["steps"]
        starts = [x["t"] for x in r["gov"].log if x["event"] == "phase A->B"] + [x["t"] for x in steps[:-1]]
        self.assertEqual(len(starts), 7)
        for st, x in zip(starts, steps):
            self.assertAlmostEqual(x["t"] - st, 30.0, delta=0.6, msg=f"step {x['rate']}/s lasted {x['t'] - st:.2f}s")

    def test_A02_default_rates_exactly_1_to_4_by_half(self):
        self.assertEqual(common.STAIRCASE, (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0))
        r = sim_run(10.0)
        self.assertEqual([x["rate"] for x in r["gov"].summary()["steps"]], [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0])

    def test_A03_4_0_fully_judged_before_escalation_stops(self):
        r = sim_run(10.0)
        g = r["gov"].summary()
        last = g["steps"][-1]
        self.assertEqual((last["rate"], last["result"]), (4.0, "PASS"))
        self.assertNotIn("note", last)                      # judged on its full quota, never cut by the cap branch
        b = [d for d in r["evidence"].records if d["phase"] == "B"]
        self.assertEqual(len(b), 525)
        self.assertTrue(any("top step 4.0/s passed" in x.get("why", "") for x in g["log"]))

    def test_A04_A05_4_5_and_5_0_never_entered(self):
        for c in (1.0, 10.0, 20.0):
            r = sim_run(c)
            rates = {d["rate"] for d in r["evidence"].decisions if "rate" in d}
            self.assertNotIn(4.5, rates, c)
            self.assertNotIn(5.0, rates, c)
            self.assertLessEqual(max(rates), 4.0, c)
        self.assertEqual(common.HARD_CEILING_PER_S, 5.0)     # the global guard stays

    def test_A06_A07_A08_full_staircase_population(self):
        r = sim_run(10.0)
        sent = r["gov"].summary()["sent"]
        self.assertEqual(sent["B"], 525)
        self.assertEqual(sent["A"] + sent["B"], 575)
        self.assertEqual(750 - sent["A"] - sent["B"], 175)
        self.assertEqual(sent["C"], 175)
        self.assertEqual((sent["T"], sent["T2"]), (0, 0))
        self.assertEqual(common.population_budget(), {"total": 750, "A": 50, "B_max": 525, "T": 0, "C_min": 175})

    def test_A09_A10_all_pass_r_star_4_and_wording(self):
        r = sim_run(10.0)
        g = r["gov"].summary()
        self.assertEqual(g["r_star"], 4.0)
        br = g["stability_bracket"]
        self.assertEqual((br["low"], br["high"], br["bracketed"]), (4.0, None, False))
        st = br["statement"]
        self.assertIn(">= 4.0 orders/s", st)
        self.assertIn("upper stability boundary not bracketed by L1", st)
        self.assertIn("not an allocator ceiling", st)
        self.assertNotRegex(st.lower().replace("not an allocator ceiling", ""), r"ceiling|maximum|capacity is")
        w = r["world"]
        m = loadmetrics.compute({"arrivals": [t for t, _ in w.created], "completions": {str(i): t for t, i in w.completed},
                                 "sampler": [], "governor": g})
        self.assertTrue(m["observed_sustainable_throughput"]["lower_bound_only"])
        self.assertEqual(m["observed_sustainable_throughput"]["value"], 4.0)
        self.assertIn("LOWER BOUND", m["observed_sustainable_throughput"]["basis"])
        self.assertEqual(m["stability_statement"]["value"], st)

    def test_A11_failure_at_3_5_gives_3_0_to_3_5(self):
        r = sim_run(30.0)            # model capacity 3.33 orders/s
        br = r["gov"].summary()["stability_bracket"]
        self.assertEqual((br["low"], br["high"], br["bracketed"]), (3.0, 3.5, True))
        self.assertEqual(br["statement"], "Stability point in [3.0, 3.5) orders/s: 3.0/s was sustained, 3.5/s was not.")
        m = loadmetrics.compute({"arrivals": [1.0], "completions": {"x": 2.0}, "sampler": [], "governor": r["gov"].summary()})
        self.assertFalse(m["observed_sustainable_throughput"]["lower_bound_only"])

    def test_A12_failure_at_4_0_gives_3_5_to_4_0(self):
        r = sim_run(27.0)            # model capacity 3.70 orders/s
        br = r["gov"].summary()["stability_bracket"]
        self.assertEqual((br["low"], br["high"], br["bracketed"]), (3.5, 4.0, True))
        self.assertEqual(r["gov"].summary()["steps"][-1]["rate"], 4.0)
        self.assertEqual(br["statement"], "Stability point in [3.5, 4.0) orders/s: 3.5/s was sustained, 4.0/s was not.")

    def test_A13_early_termination_grows_C(self):
        full = sim_run(10.0)["gov"].summary()["sent"]["C"]
        self.assertEqual(full, 175)
        for c in (36.0, 48.0, 80.0):
            g = sim_run(c)["gov"].summary()
            self.assertLess(g["sent"]["B"], 525, c)
            self.assertEqual(g["sent"]["A"] + g["sent"]["B"] + g["sent"]["C"], 750, c)
            self.assertEqual(g["sent"]["C"], 750 - 50 - g["sent"]["B"], c)
            self.assertGreater(g["sent"]["C"], full, c)
        g = sim_run(36.0)["gov"].summary()           # 3.0/s fails on its full quota: B = 30+45+60+75+90
        self.assertEqual((g["sent"]["B"], g["sent"]["C"]), (300, 400))

    def test_A14_T_T2_excluded_from_default_budget(self):
        g = Governor()
        self.assertEqual((g.reserve_t, g.budget["T"], g.budget["C_min"]), (0, 0, 175))
        r = sim_run(10.0)
        self.assertEqual(r["gov"].summary()["sent"]["T"] + r["gov"].summary()["sent"]["T2"], 0)
        with self.assertRaises(Refused):
            Governor(t_budget=40)                            # a T budget without T approval is refused

    def test_A15_enabling_T_requires_its_40_orders_reserved(self):
        with self.assertRaises(Refused):
            Governor(approve_t=APPROVE_T)                    # approved but not budgeted
        with self.assertRaises(Refused):
            Governor(approve_t=APPROVE_T, t_budget=20)
        g = Governor(approve_t=APPROVE_T, t_budget=40)
        self.assertEqual(g.budget, {"total": 750, "A": 50, "B_max": 525, "T": 40, "C_min": 135})
        r = sim_run(10.0, gov=Governor(approve_t=APPROVE_T, t_budget=40))
        sent = r["gov"].summary()["sent"]
        self.assertEqual((sent["A"], sent["B"], sent["C"], sent["T"]), (50, 525, 135, 40))

    def test_A16_total_never_exceeds_750(self):
        for n in (751, 1000):
            with self.assertRaises(Refused):
                common.population_budget(n)
        with self.assertRaises(Refused):
            Governor(n_orders=600, approve_t=APPROVE_T, t_budget=40)      # 50 + 525 + 40 > 600
        with self.assertRaises(Refused):
            Governor(n_orders=574)
        for c in (1.0, 10.0, 28.0, 48.0, 200.0):
            for mk in (lambda: Governor(), lambda: Governor(approve_t=APPROVE_T, t_budget=40)):
                r = sim_run(c, gov=mk())
                self.assertLessEqual(r["gov"].total_sent(), 750, c)
                self.assertLessEqual(r["summary"]["dispatched"], 750, c)
                self.assertEqual(len(r["client"].calls), len(set(r["client"].calls)), c)


# =================================================================================================================================
def ev(ts, topic="orders/paid", wid=None, status=200, wall=800, rid=None, phase=None):
    return {"ts": ts, "topic": topic, "webhook_id": wid or f"w{ts}", "status": status, "wall_ms": wall,
            "request_id": rid or f"r{ts}{topic}", "phase": phase}


class FailWatchTests(unittest.TestCase):
    def test_R09_two_consecutive_safety(self):
        f = failwatch.FailWatch()
        f.observe(ev(1, status=500))
        self.assertEqual(f.level, "LOAD_STOP")
        f.observe(ev(2, status=503))
        self.assertEqual(f.level, "SAFETY_STOP")
        # a success in between resets the consecutive count (but not the total)
        f = failwatch.FailWatch()
        f.observe(ev(1, status=500)); f.observe(ev(2)); f.observe(ev(3, status=500))
        self.assertEqual(f.level, "LOAD_STOP")
        self.assertEqual(f.state()["max_consecutive"]["orders/paid"], 1)
        # per topic: one failure on each of two topics is not 'consecutive'
        f = failwatch.FailWatch()
        f.observe(ev(1, topic="orders/paid", status=500)); f.observe(ev(2, topic="orders/create", status=500))
        self.assertEqual(f.level, "LOAD_STOP")

    def test_R10_three_total_safety(self):
        f = failwatch.FailWatch()
        for t, st in ((1, 500), (2, 200), (3, 500), (4, 200), (5, 500)):
            f.observe(ev(t, status=st))
        self.assertEqual(f.level, "SAFETY_STOP")
        self.assertIn("3 failures in total", " ".join(f.reasons))

    def test_R11_same_webhook_twice_safety(self):
        f = failwatch.FailWatch()
        f.observe(ev(1, wid="W1", status=500))
        f.observe(ev(2))
        f.observe(ev(70, wid="W1", status=500, rid="retry"))
        self.assertEqual(f.level, "SAFETY_STOP")
        self.assertEqual(f.state()["repeated_webhooks"], ["W1"])

    def test_R12_single_failure_load_stop_and_T2_budget(self):
        f = failwatch.FailWatch()
        f.observe(ev(1, status=500))
        self.assertEqual(f.level, "LOAD_STOP")
        f = failwatch.FailWatch()
        f.observe(ev(1, wall=5001))                      # Shopify's 5 s timeout counts as a failure
        self.assertEqual(f.level, "LOAD_STOP")
        f = failwatch.FailWatch()
        f.observe(ev(1, status=500, phase="T2"))         # T2 not approved: LOAD STOP
        self.assertEqual(f.level, "LOAD_STOP")
        f = failwatch.FailWatch(t2_approved=True)
        f.observe(ev(1, status=500, phase="T2"))         # the one approved T2 failure: WARNING only
        self.assertEqual(f.level, "WARNING")
        f.observe(ev(2))
        f.observe(ev(3, status=500, phase="T2"))         # budget used: LOAD STOP
        self.assertEqual(f.level, "LOAD_STOP")
        f.observe(ev(4, status=500, phase="T2"))         # consecutive rule still applies inside T2
        self.assertEqual(f.level, "SAFETY_STOP")

    def test_failure_after_load_stop_is_safety(self):
        f = failwatch.FailWatch()
        f.observe(ev(1, status=500))
        f.note_load_stop(1)
        f.observe(ev(2))
        f.observe(ev(40, status=500, topic="orders/create"))
        self.assertEqual(f.level, "SAFETY_STOP")

    def test_tail_and_wl_dedup_and_confirmation(self):
        f = failwatch.FailWatch()
        e = ev(1, status=500, rid="ray1")
        f.observe(e, "tail")
        missing = f.confirm_wl([dict(e)], True)
        self.assertEqual(missing, [])
        self.assertEqual(f.state()["total"]["orders/paid"], 1, "the same invocation from tail and WL counted twice")
        f2 = failwatch.FailWatch()
        f2.confirm_wl([ev(5, status=500, rid="only-wl")], True)      # WL-only failure is added
        self.assertEqual(f2.level, "LOAD_STOP")
        f3 = failwatch.FailWatch()
        f3.confirm_wl([], False)                                   # incomplete WL window = cannot confirm = LOAD STOP
        self.assertEqual(f3.level, "LOAD_STOP")

    def test_levels_never_fall(self):
        f = failwatch.FailWatch()
        f.observe(ev(1, status=500)); f.observe(ev(2, status=500))
        for t in range(3, 50):
            f.observe(ev(t))
        self.assertEqual(f.level, "SAFETY_STOP")

    def test_thresholds_below_shopify_deletion(self):
        self.assertLess(common.SAFETY_CONSECUTIVE, common.SHOPIFY_DELETE_AFTER)
        self.assertLess(common.SAFETY_TOTAL, common.SHOPIFY_DELETE_AFTER)
        self.assertEqual(common.SHOPIFY_DELETE_AFTER, 8)


# =================================================================================================================================
class DriverGateTests(unittest.TestCase):
    def setUp(self):
        self.d = tmpdir()
        self.plan = bound_plan()
        self.sha = plan_hash(self.plan)
        self.marker = os.path.join(self.d, f"load-{L1_CID}.done")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_R17_one_shot_marker(self):
        load.check_offline_gates(self.plan, self.sha, CONFIRM_LIVE, self.marker)
        load.create_marker(self.marker, self.sha, 1.0)
        with self.assertRaises(Refused):
            load.check_offline_gates(self.plan, self.sha, CONFIRM_LIVE, self.marker)
        with self.assertRaises(FileExistsError):
            load.create_marker(self.marker, self.sha, 2.0)
        clock, world = FakeClock(), None
        with self.assertRaises(FileExistsError):      # run() itself refuses before any send
            load.run(self.plan, SimClient(World(FakeClock())), Governor(), lambda n, a: Signals(), clock, self.marker, load.Evidence(None))

    def test_R18_phrase_mandatory(self):
        for bad in (None, "", "LOAD-QAL-750-ORDERS", CONFIRM_LIVE.lower(), CONFIRM_STAGE):
            with self.assertRaises(Refused):
                load.check_offline_gates(self.plan, self.sha, bad, self.marker)
        with self.assertRaises(Refused):
            load.check_offline_gates(self.plan, "0" * 64, CONFIRM_LIVE, self.marker)
        with self.assertRaises(Refused):
            load.check_offline_gates(self.plan, self.sha, CONFIRM_LIVE, self.marker, approve_t="nope")
        with self.assertRaises(Refused):
            load.check_offline_gates(self.plan, self.sha, CONFIRM_LIVE, self.marker, approve_t2=APPROVE_T2)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = load.main(["live", "--plan", "/nonexistent", "--plan-sha", "x", "--state-dir", self.d, "--confirm", "no",
                            "--restore-cmd", "false"])
        self.assertEqual(rc, 2, "the phrase is checked before anything else, even before reading the plan")
        self.assertEqual(CONFIRM_LIVE, "LOAD-QAL-750-ORDERS-1650-ENTRIES")

    def test_R19_allow_list_rejects_old_fixtures(self):
        for cid, name in common.RETIRED_COMPETITIONS.items():
            p = bound_plan(cid)
            self.assertTrue(any("retired" in x for x in validate_plan(p, bound=True)), name)
            with self.assertRaises(Refused):
                load.check_offline_gates(p, plan_hash(p), CONFIRM_LIVE, self.marker)
            with self.assertRaises(Refused):
                regsql.build(cid, "2026-10-06T10:00:00.000Z")
            prod = good_product(cid)
            self.assertTrue(any("retired" in x for x in stage.check_product(prod)))
            self.assertTrue(canary.check_canary({**good_canary(), "id": f"gid://shopify/Product/{cid}"}, [], L1_CID))
        for pfx in ("QAE", "QAF", "QAG", "QAH", "QAI", "QAJ", "PUT"):
            self.assertIn(pfx, common.RETIRED_PREFIXES)
            p = bound_plan()
            p["prefix"] = pfx
            self.assertTrue(validate_plan(p, bound=True))
        p = bound_plan()
        p["rows"][3]["draft_id"] = "gid://shopify/DraftOrder/1614665220470"     # QAJ #D32
        self.assertFalse(common.allowed_draft(bound_plan(), "gid://shopify/DraftOrder/1614665220470"))

    def test_R20_non_plan_draft_never_sent(self):
        p = bound_plan()
        sent = []

        class C:
            secrets = []

            def post(self, doc, var=None):
                sent.append(var["id"])
                return 200, {"x-request-id": "r"}, {"data": {"draftOrderComplete": {"draftOrder": {"status": "COMPLETED"}, "userErrors": []}}}
        # a plan tampered after approval (a QAJ draft swapped in) is refused by the gates before anything is sent
        q = json.loads(json.dumps(p))
        q["rows"][0]["draft_id"] = "gid://shopify/DraftOrder/1614665220470"
        with self.assertRaises(Refused):
            load.check_offline_gates(q, self.sha, CONFIRM_LIVE, self.marker)
        # and a QAJ draft is never an allowed draft of the approved plan
        self.assertFalse(common.allowed_draft(p, "gid://shopify/DraftOrder/1614665220470"))
        self.assertEqual(sent, [])

    def test_classify_outcomes(self):
        ok = {"data": {"draftOrderComplete": {"draftOrder": {"status": "COMPLETED"}, "userErrors": []}}}
        self.assertEqual(load.classify(200, {"x-request-id": "a"}, ok), ("SUCCESS", "a"))
        self.assertEqual(load.classify(200, {}, ok)[0], "UNKNOWN")
        self.assertEqual(load.classify(200, {"x-request-id": "a"}, {"errors": [{"extensions": {"code": "THROTTLED"}}]})[0],
                         "THROTTLED_NOT_EXECUTED")
        self.assertEqual(load.classify(500, {"x-request-id": "a"}, ok)[0], "ERROR")
        self.assertEqual(load.classify(200, {"x-request-id": "a"}, {"data": {"draftOrderComplete": {"draftOrder": {"status": "OPEN"}}}})[0], "ERROR")

    def test_evidence_scrubs_secrets(self):
        e = load.Evidence(os.path.join(self.d, "e.jsonl"), ["tok-SECRET-123456"])
        e.add("request", {"x": "Bearer tok-SECRET-123456", "outcome": "SUCCESS"})
        self.assertNotIn("tok-SECRET", open(os.path.join(self.d, "e.jsonl")).read())

    def test_file_signals_fail_safe(self):
        fs = load.FileSignals(self.d)
        s = fs.read(1000.0, [])
        self.assertTrue(any("guardl heartbeat" in x for x in s.safety), "a missing guard must read as SAFETY")
        self.assertEqual(s.bucket, [])
        common.write_json(os.path.join(self.d, "guardl.json"), {"ts": 999.0, "safety": [], "load": []})
        with open(os.path.join(self.d, "sampler.jsonl"), "w") as f:
            f.write(json.dumps({"ts": 999.0, "available": 1990}) + "\n")
        s = fs.read(1000.0, [])
        self.assertEqual((s.safety, s.load, s.bucket), ([], [], [(999.0, 1990)]))
        open(os.path.join(self.d, "load-stop"), "w").close()
        self.assertTrue(fs.read(1000.0, []).load)
        open(os.path.join(self.d, "manual-stop"), "w").close()
        self.assertIn("manual-stop present", fs.read(1000.0, []).safety)


# =================================================================================================================================
class ClampTests(unittest.TestCase):
    def client(self, avail_seq, throttle_at=None):
        n = {"i": 0}

        def h(body):
            n["i"] += 1
            av = avail_seq[min(n["i"] - 1, len(avail_seq) - 1)]
            return 200, {"x-request-id": "r"}, cost(av, throttled=(throttle_at is not None and n["i"] >= throttle_at))
        tx = FakeTx(h)
        c = shop.Client("allocator", {clampmod.CLAMP_DOC}, ENV, tx)
        return c, tx

    def clock_step(self, step):
        class C:
            t = 0.0

            def now(self):
                self.t += step
                return self.t
        return C()

    def test_R21_clamp_requires_T_approval(self):
        c, _ = self.client([1000])
        for bad in (None, "", "APPROVE", APPROVE_T2):
            with self.assertRaises(Refused):
                clampmod.Clamp(c, self.clock_step(0.1), lambda: "T", approve_t=bad)

    def test_R22_T2_window_requires_T2_approval(self):
        c, _ = self.client([1000])
        cl = clampmod.Clamp(c, self.clock_step(0.1), lambda: "T2", approve_t=APPROVE_T)
        with self.assertRaises(Refused):
            cl.window("T2")
        with self.assertRaises(Refused):
            clampmod.Clamp(c, self.clock_step(0.1), lambda: "T2", approve_t=APPROVE_T, approve_t2="x")
        cl = clampmod.Clamp(c, self.clock_step(0.1), lambda: "T2", approve_t=APPROVE_T, approve_t2=APPROVE_T2)
        r = cl.window("T2")
        self.assertLessEqual(r["duration_s"], common.CLAMP_T2_MAX_WINDOW_S + 0.2)

    def test_R23_releases_on_throttled(self):
        c, tx = self.client([900, 500, 100, 20], throttle_at=4)
        cl = clampmod.Clamp(c, self.clock_step(0.05), lambda: "T", approve_t=APPROVE_T)
        r = cl.window("T")
        self.assertEqual(r["release"], "throttled (clamp's own request)")
        self.assertEqual(r["requests"], 4)
        c2, tx2 = self.client([900])
        cl2 = clampmod.Clamp(c2, self.clock_step(0.05), lambda: "T", approve_t=APPROVE_T, throttled_seen=lambda since: len(tx2.sent) >= 2)
        r2 = cl2.window("T")
        self.assertEqual(r2["release"], "throttled (allocator)")
        self.assertEqual(r2["requests"], 2)

    def test_R24_hard_1_5s_maximum(self):
        c, tx = self.client([1500])
        clk = self.clock_step(0.1)
        cl = clampmod.Clamp(c, clk, lambda: "T", approve_t=APPROVE_T)
        r = cl.window("T")
        self.assertEqual(r["release"], "timeout")
        starts = 0.0
        self.assertLess(r["requests"], 16)
        self.assertLessEqual(r["duration_s"], 1.5 + 0.3)   # no request STARTS at/after 1.5 s (two clock reads per loop)
        cl.window("T")
        with self.assertRaises(Refused):
            cl.window("T")                                  # two windows in T at most

    def test_R25_refuses_outside_phase_T(self):
        c, tx = self.client([1000])
        for ph in ("A", "B", "C", "E", "DONE", "T2"):
            cl = clampmod.Clamp(c, self.clock_step(0.1), lambda ph=ph: ph, approve_t=APPROVE_T)
            with self.assertRaises(Refused):
                cl.window("T")
        self.assertEqual(tx.sent, [], "a request was sent outside phase T")

    def test_clamp_can_only_send_its_read_only_document(self):
        self.assertTrue(shop.read_only_graphql(clampmod.CLAMP_DOC)[0])
        with self.assertRaises(Refused):
            shop.Client("allocator", {"mutation X { draftOrderComplete(id: 1) { userErrors { message } } }"}, ENV, FakeTx(lambda b: (200, {}, {})))
        c, tx = self.client([1000])
        with self.assertRaises(Refused):
            c.post("{ shop { id } }")                      # not in this client's allow-list
        sd = shop.Client("stress_driver", {load.MUTATION}, ENV, FakeTx(lambda b: (200, {}, {}), "write_draft_orders"))
        with self.assertRaises(Refused):
            clampmod.Clamp(type("X", (), {"app": "stress_driver"})(), self.clock_step(0.1), lambda: "T", approve_t=APPROVE_T)
        cl = clampmod.Clamp(c, self.clock_step(0.6), lambda: "T", approve_t=APPROVE_T)
        cl.window("T")
        self.assertTrue(all(b["query"] == clampmod.CLAMP_DOC for b in tx.sent))


# =================================================================================================================================
class SamplerTests(unittest.TestCase):
    def test_sampler_cost_and_subscription_check(self):
        exp = [["gid://shopify/WebhookSubscription/1", "ORDERS_PAID", "https://qa.example/webhooks/orders/paid"]]
        state = {"subs": exp}

        def h(body):
            b = cost(1800)
            b["extensions"]["cost"]["actualQueryCost"] = 2 if "webhookSubscriptions" in body["query"] else 1
            if "webhookSubscriptions" in body["query"]:
                b["data"]["webhookSubscriptions"] = {"nodes": [{"id": i, "topic": t, "endpoint": {"callbackUrl": u}} for i, t, u in state["subs"]]}
            return 200, {}, b
        c = shop.Client("allocator", sampler.DOCUMENTS, ENV, FakeTx(h))
        clock = FakeClock(0.0)
        d = tmpdir()
        s = sampler.Sampler(c, clock, os.path.join(d, "sampler.jsonl"), exp)
        n = {"k": 0}

        def until():
            n["k"] += 1
            if n["k"] == 20:
                state["subs"] = []                         # a subscription disappears
            return n["k"] > 40
        s.run(until)
        recs = [json.loads(x) for x in open(os.path.join(d, "sampler.jsonl"))]
        self.assertEqual(len(recs), 40)
        self.assertTrue(all(r["available"] == 1800 for r in recs))
        subs = [r for r in recs if "subs" in r]
        self.assertGreaterEqual(len(subs), 2)
        self.assertTrue(subs[0]["subs_ok"])
        self.assertFalse(subs[-1]["subs_ok"])
        cst = sampler.sampler_cost(recs)
        self.assertEqual(cst["points"], 40 + len(subs))
        self.assertAlmostEqual(cst["pts_per_s"], cst["points"] / (recs[-1]["ts"] - recs[0]["ts"]))
        self.assertNotIn("SECRET", open(os.path.join(d, "sampler.jsonl")).read())
        shutil.rmtree(d)


# =================================================================================================================================
def good_product(cid=L1_CID):
    return {"id": f"gid://shopify/Product/{cid}", "title": common.PRODUCT_TITLE, "handle": common.PRODUCT_HANDLE, "status": "ACTIVE",
            "publishedAt": None, "onlineStoreUrl": None, "publications": 0, "tags": ["qa-only", "qa-load", "QAL"],
            "metafields": {"entry_prefix": "QAL", "entry_start_number": "1001", "entries_total": "2000", "skill_mode": "none"},
            "variants": [{"id": "gid://shopify/ProductVariant/1", "price": "1.00", "inventoryPolicy": "DENY", "inventoryQuantity": 1650,
                          "tracked": True, "requiresShipping": False}]}


def good_canary():
    return {"id": "gid://shopify/Product/888000111", "title": common.CANARY_TITLE, "publishedAt": None, "publications": 0,
            "variants": [{"id": "gid://shopify/ProductVariant/2", "price": "1.00", "requiresShipping": False}]}


class StageCanaryTests(unittest.TestCase):
    def test_stage_payloads_zero_value_no_customer(self):
        d = tmpdir()
        r = stage.stage(plan_template(), good_product(), None, FakeClock(), d)
        self.assertEqual(r, {"mode": "dry", "payloads": 750})
        pl = json.load(open(os.path.join(d, "stage-payloads.json")))
        self.assertEqual(len(pl), 750)
        for p, row in zip(pl, plan_template()["rows"]):
            inp = p["input"]
            self.assertEqual(set(inp), {"lineItems", "appliedDiscount", "tags", "note"})
            for k in ("customerId", "email", "phone", "shippingAddress", "billingAddress", "shippingLine", "purchasingEntity"):
                self.assertNotIn(k, inp)
            self.assertEqual(inp["appliedDiscount"], {"valueType": "PERCENTAGE", "value": 100.0, "title": common.DISCOUNT_TITLE})
            self.assertEqual(inp["lineItems"], [{"variantId": "gid://shopify/ProductVariant/1", "quantity": row["qty"]}])
            self.assertEqual(inp["tags"], ["qa-load", "QAL", row["tag"]])
        shutil.rmtree(d)

    def test_stage_product_checks(self):
        self.assertEqual(stage.check_product(good_product()), [])
        for k, v in (("status", "DRAFT"), ("publications", 1), ("publishedAt", "2026-01-01")):
            self.assertTrue(stage.check_product({**good_product(), k: v}), k)
        for k, v in (("requiresShipping", True), ("price", "0.00"), ("inventoryQuantity", 1649), ("tracked", False), ("inventoryPolicy", "CONTINUE")):
            p = good_product()
            p["variants"][0][k] = v
            self.assertTrue(stage.check_product(p), k)
        p = good_product()
        p["metafields"]["skill_mode"] = None
        self.assertTrue(stage.check_product(p))

    def test_stage_execute_refusals_and_binding(self):
        d = tmpdir()
        with self.assertRaises(Refused):
            stage.stage(plan_template(), good_product(), None, FakeClock(), d, execute=True, confirm="no")
        state = {"existing": [{"id": "gid://shopify/DraftOrder/1"}], "n": 0}

        def h(body):
            if "qaload:" in body["query"]:
                return 200, {}, {"data": {"qal": {"nodes": state["existing"]}, "qaload": {"nodes": []}}}
            state["n"] += 1
            inp = body["variables"]["input"]
            return 200, {"x-request-id": f"q{state['n']}"}, {"data": {"draftOrderCreate": {"draftOrder": {
                "id": f"gid://shopify/DraftOrder/{77000 + state['n']}", "name": f"#D{state['n']}", "status": "OPEN", "tags": inp["tags"],
                "totalPriceSet": {"shopMoney": {"amount": "0.0"}}}, "userErrors": []}}}
        c = shop.Client("stress_driver", {stage.DRAFT_CREATE, stage.LIST_QAL}, ENV, FakeTx(h, "write_draft_orders"))
        with self.assertRaises(Refused):                  # QAL drafts already exist
            stage.stage(plan_template(), good_product(), c, FakeClock(), d, True, CONFIRM_STAGE)
        state["existing"] = []
        r = stage.stage(plan_template(), good_product(), c, FakeClock(), d, True, CONFIRM_STAGE)
        self.assertEqual(r["status"], "COMPLETE")
        bound = json.load(open(os.path.join(d, "plan-bound.json")))
        self.assertEqual(validate_plan(bound, bound=True), [])
        self.assertEqual(plan_hash(bound), open(os.path.join(d, "plan-bound.sha256")).read().strip())
        self.assertEqual(r["plan_sha256"], plan_hash(bound))
        shutil.rmtree(d)

    def test_stage_stops_on_first_error_no_retry(self):
        d = tmpdir()
        n = {"k": 0}

        def h(body):
            if "qaload:" in body["query"]:
                return 200, {}, {"data": {"qal": {"nodes": []}, "qaload": {"nodes": []}}}
            n["k"] += 1
            if n["k"] == 3:
                return 200, {"x-request-id": "e"}, {"data": {"draftOrderCreate": {"draftOrder": None, "userErrors": [{"message": "x"}]}}}
            return 200, {"x-request-id": "o"}, {"data": {"draftOrderCreate": {"draftOrder": {"id": f"gid://shopify/DraftOrder/{n['k']}",
                    "status": "OPEN", "tags": body["variables"]["input"]["tags"], "totalPriceSet": {"shopMoney": {"amount": "0.00"}}},
                    "userErrors": []}}}
        c = shop.Client("stress_driver", {stage.DRAFT_CREATE, stage.LIST_QAL}, ENV, FakeTx(h, "write_draft_orders"))
        r = stage.stage(plan_template(), good_product(), c, FakeClock(), d, True, CONFIRM_STAGE)
        self.assertEqual((r["status"], r["created"], r["stopped_at"]), ("INCOMPLETE", 2, plan_template()["rows"][2]["index"]))
        self.assertEqual(n["k"], 3)
        self.assertFalse(os.path.exists(os.path.join(d, "plan-bound.json")))
        shutil.rmtree(d)

    def test_canary_gates(self):
        d = tmpdir()
        sent = []

        def h(body):
            sent.append(body["query"][:30])
            if "draftOrderCreate" in body["query"]:
                return 200, {"x-request-id": "c1"}, {"data": {"draftOrderCreate": {"draftOrder": {"id": "gid://shopify/DraftOrder/5",
                        "status": "OPEN", "totalPriceSet": {"shopMoney": {"amount": "0.0"}}}, "userErrors": []}}}
            return 200, {"x-request-id": "c2"}, {"data": {"draftOrderComplete": {"draftOrder": {"status": "COMPLETED"}, "userErrors": []}}}
        c = shop.Client("stress_driver", {stage.DRAFT_CREATE, load.MUTATION}, ENV, FakeTx(h, "write_draft_orders"))
        # now that the Stage 2 canary exists, any further canary run is refused before anything is sent
        with self.assertRaises(Refused):
            canary.run_canary(good_canary(), [], L1_CID, c, lambda: "true", d, CONFIRM_CANARY, FakeClock())
        self.assertEqual(sent, [])
        # the original single-run gates, each exercised on its own (as they stood before Stage 2)
        with mock.patch.dict(common.KNOWN_CANARY, {"order_gid": None}):
            with self.assertRaises(Refused):
                canary.run_canary(good_canary(), [], L1_CID, c, lambda: "true", d, "no", FakeClock())
            with self.assertRaises(Refused):                # registered in D1
                canary.run_canary(good_canary(), ["888000111"], L1_CID, c, lambda: "true", d, CONFIRM_CANARY, FakeClock())
            with self.assertRaises(Refused):                # is the L1 competition
                canary.run_canary(good_canary(), [], "888000111", c, lambda: "true", d, CONFIRM_CANARY, FakeClock())
            with self.assertRaises(Refused):                # QA Worker live
                canary.run_canary(good_canary(), [], L1_CID, c, lambda: "false", d, CONFIRM_CANARY, FakeClock())
            self.assertEqual(sent, [])
            r = canary.run_canary(good_canary(), [], L1_CID, c, lambda: "true", d, CONFIRM_CANARY, FakeClock())
        self.assertEqual(r["result"], "COMPLETED")
        self.assertEqual(len(sent), 2)
        inp = canary.canary_input("v")
        self.assertEqual(set(inp), {"lineItems", "appliedDiscount", "tags", "note"})
        shutil.rmtree(d)


# =================================================================================================================================
def migrations():
    names = ["0001_competition.sql", "0002_entry_number.sql", "0003_allocation.sql", "0004_entry_event.sql",
             "0005_skill_verdict_not_required.sql"]
    out = []
    for n in names:
        r = subprocess.run(["git", "-C", GIT_REPO, "show", f"{ALLOC_COMMIT}:greenside-entry-allocator/migrations/{n}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"cannot read allocator migration {n} at {ALLOC_COMMIT}: {r.stderr[:200]}")
        out.append(r.stdout)
    return out


SNAP_SQL = open(os.path.join(REPO, "qa", "b3-stress-harness", "b3stress", "snap.sql")).read()


def snapshot(conn):
    cur = conn.execute(SNAP_SQL)
    cols = [d[0] for d in cur.description]
    row = dict(zip(cols, cur.fetchone()))
    for k in ("competitions", "entries", "allocations", "events", "put_summary", "integrity", "webhook_deliveries", "sqlite_seq"):
        if isinstance(row.get(k), str):
            row[k] = json.loads(row[k])
    return row


def oid(i):
    return str(13600000000000 + i)


def lid(i):
    return str(37000000000000 + i)


class World750:
    """A QA D1 (SQLite, allocator migrations), an unrelated pre-existing competition with one allocation, the L1 registration from
    regsql, and the 750 plan orders allocated lowest-first in a shuffled completion order (as the allocator claims)."""

    def __init__(self, n_orders=750):
        self.conn = sqlite3.connect(":memory:")
        for m in migrations():
            self.conn.executescript(m)
        c = self.conn
        c.executescript(regsql.competition_sql("15897614614902", "2026-09-20T10:00:00.000Z").replace("'QAL'", "'QAE'").replace("2000, 4", "3, 4"))
        for s in range(1001, 1004):
            c.execute("INSERT INTO entry_number (competition_id, seq, entry_number, status, allocation_seq) VALUES (?, ?, ?, 'AVAILABLE', 0)",
                      ("15897614614902", s, f"QAE{s}"))
        c.execute("UPDATE competition SET status='OPEN' WHERE competition_id='15897614614902'")
        self._alloc("15897614614902", "13500000000001", "#1001", "36000000000001", 1, "QAE", [1001], "2026-09-20T11:00:00.000Z")
        for name, sql in regsql.build(L1_CID, "2026-10-06T10:00:00.000Z"):
            c.executescript(sql)
        self.pre = snapshot(c)
        self.plan = bound_plan()
        rows = self.plan["rows"][:n_orders]
        order = list(range(len(rows)))
        random.Random(11).shuffle(order)
        nxt = 1001
        for k in order:
            r = rows[k]
            seqs = list(range(nxt, nxt + r["qty"]))
            nxt += r["qty"]
            self._alloc(L1_CID, oid(r["index"]), f"#{1028 + r['index']}", lid(r["index"]), r["qty"], "QAL", seqs,
                        f"2026-10-06T11:{k // 60:02d}:{k % 60:02d}.000Z")
        self.rows = rows

    def _alloc(self, cid, order_id, name, line_id, qty, pfx, seqs, at):
        aid = hashlib.sha256(f"gsalloc:v1:shop:{order_id}:{line_id}".encode()).hexdigest()
        c = self.conn
        c.execute("INSERT INTO allocation (allocation_id, shop_domain, competition_id, order_id, order_name, order_created_at, line_item_id, "
                  "variant_id, customer_ref, entry_route, ordered_quantity, entries_per_unit, target_count, held_count, skill_question, "
                  "skill_answer, skill_answer_correct_snapshot, skill_verdict, skill_judged_at, skill_rule_version, unit_price_minor, "
                  "line_total_minor, decision_basis, status, source, mirror_state, mirror_attempts, created_at, updated_at) VALUES "
                  "(?, 'shop', ?, ?, ?, ?, ?, '1', NULL, 'unknown', ?, 1, ?, ?, NULL, NULL, NULL, 'NOT_REQUIRED', NULL, NULL, 0, 0, '{}', "
                  "'ALLOCATED', 'webhook', 'PENDING', 0, ?, ?)", (aid, cid, order_id, name, at, line_id, qty, qty, qty, at, at))
        for s in seqs:
            c.execute("UPDATE entry_number SET status='ALLOCATED', allocation_id=?, order_id=?, line_item_id=?, allocation_seq=allocation_seq+1, "
                      "allocated_at=? WHERE competition_id=? AND seq=?", (aid, order_id, line_id, at, cid, s))
            c.execute("INSERT INTO entry_event (occurred_at, competition_id, seq, entry_number, allocation_id, allocation_seq, event_type, "
                      "from_status, to_status, order_id, customer_ref, reason, actor, run_id, webhook_id, detail_json) VALUES "
                      "(?, ?, ?, ?, ?, 1, 'ALLOCATED', 'AVAILABLE', 'ALLOCATED', ?, NULL, NULL, 'system:webhook', ?, ?, '{}')",
                      (at, cid, s, f"{pfx}{s}", aid, order_id, f"run-{order_id}", f"wh-{order_id}"))
        return aid

    def inputs(self):
        post = snapshot(self.conn)
        orders, drafts, driver = [], [], []
        done = {r["index"] for r in self.rows}
        for r in self.plan["rows"]:
            drafts.append({"id": r["draft_id"], "status": "COMPLETED" if r["index"] in done else "OPEN"})
            if r["index"] in done:
                driver.append({"kind": "request", "index": r["index"], "draft_id": r["draft_id"], "qty": r["qty"], "outcome": "SUCCESS",
                               "shopify_request_id": f"req-{r['index']}", "t_send": 1.0})
                orders.append({"id": f"gid://shopify/Order/{oid(r['index'])}", "name": f"#{1028 + r['index']}", "test": False,
                               "cancelledAt": None, "displayFinancialStatus": "PAID", "tags": ["qa-load", "QAL", r["tag"]],
                               "totalPriceSet": {"shopMoney": {"amount": "0.0"}}, "totalTaxSet": {"shopMoney": {"amount": "0.0"}},
                               "lineItems": {"nodes": [{"id": f"gid://shopify/LineItem/{lid(r['index'])}", "quantity": r["qty"],
                                                        "product": {"id": self.plan["product_gid"]}}]}})
        units = sum(r["qty"] for r in self.rows)
        wl = [{"webhook_id": f"wh-{oid(r['index'])}", "run_id": f"run-{oid(r['index'])}"} for r in self.rows]
        return {"plan": self.plan, "driver": driver, "orders": orders, "drafts": drafts, "d1_pre": self.pre, "d1_post": post,
                "product": {"inventoryQuantity": 1650 - units, "stock_start": 1650}, "wl": wl}

    def sql(self, units=None, orders=None):
        units = sum(r["qty"] for r in self.rows) if units is None else units
        p = loadrecon.params_for(L1_CID, units, len(self.rows) if orders is None else orders)
        return {x["check"]: x["result"] for x in loadrecon.run_sql(self.conn, p)}


def failed(r):
    return sorted(c["check"] for c in r["checks"] if c["result"] == "FAIL")


class ReconTests(unittest.TestCase):
    def test_R28_synthetic_750_reconciliation_passes(self):
        w = World750()
        r = loadrecon.reconcile(w.inputs())
        self.assertEqual(failed(r), [])
        self.assertEqual(r["result"], "PASS")
        self.assertEqual((r["expected_orders"], r["expected_units"]), (750, 1650))
        self.assertEqual(r["stage_counts"], {"driver_confirmed": 750, "shopify_orders": 750, "d1_allocations": 750, "d1_units": 1650, "events": 1650})
        sq = w.sql()
        self.assertTrue(all(v == "PASS" for v in sq.values()), sq)
        self.assertGreaterEqual(len(sq), 19)

    def test_R28b_partial_run_reconciles_against_completed_rows(self):
        w = World750(n_orders=300)
        r = loadrecon.reconcile(w.inputs())
        self.assertEqual(failed(r), [])
        self.assertEqual(r["expected_orders"], 300)

    def test_R29_duplicate_allocation_fails(self):
        w = World750()
        # a second allocation for an existing order line (the same order claimed twice) holding the next free numbers
        # the schema's UNIQUE(order_id, line_item_id) refuses a second allocation row for the same line, so the duplicate is
        # modelled as the defect that remains possible: the same order claiming an extra number (over-allocation)
        r0 = w.rows[0]
        with self.assertRaises(sqlite3.IntegrityError):
            w._alloc(L1_CID, oid(r0["index"]), "#x", lid(r0["index"]), 1, "QAL", [2651], "2026-10-06T12:00:00.000Z")
        aid = w.conn.execute("SELECT allocation_id FROM allocation WHERE order_id=?", (oid(r0["index"]),)).fetchone()[0]
        w.conn.execute("UPDATE entry_number SET status='ALLOCATED', allocation_id=?, order_id=?, line_item_id=?, allocation_seq=1, "
                       "allocated_at='x' WHERE competition_id=? AND seq=2651", (aid, oid(r0["index"]), lid(r0["index"]), L1_CID))
        r = loadrecon.reconcile(w.inputs())
        self.assertEqual(r["result"], "FAIL")
        for c in ("d1.pool_counts", "d1.exact_quantity_per_order", "d1.no_out_of_range"):
            self.assertIn(c, failed(r))
        sq = w.sql()
        for c in ("pool_counts", "allocated_range", "ledger_counts", "audit_gaps_global"):
            self.assertEqual(sq[c], "FAIL", c)

    def test_R30_missing_allocation_fails(self):
        w = World750()
        r0 = w.rows[5]
        aid = w.conn.execute("SELECT allocation_id FROM allocation WHERE order_id=?", (oid(r0["index"]),)).fetchone()[0]
        w.conn.execute("UPDATE entry_number SET status='AVAILABLE', allocation_id=NULL, order_id=NULL, line_item_id=NULL, allocation_seq=0, "
                       "allocated_at=NULL WHERE allocation_id=?", (aid,))
        w.conn.execute("DELETE FROM entry_event WHERE allocation_id=?", (aid,))
        w.conn.execute("DELETE FROM allocation WHERE allocation_id=?", (aid,))
        r = loadrecon.reconcile(w.inputs())
        self.assertIn("d1.allocation_set_equals_orders", failed(r))
        self.assertIn("cross.stage_counts", failed(r))
        self.assertIn("FAIL", w.sql().values())

    def test_R31_wrong_quantity_fails(self):
        w = World750()
        r0 = next(r for r in w.rows if r["qty"] == 3)
        aid = w.conn.execute("SELECT allocation_id FROM allocation WHERE order_id=?", (oid(r0["index"]),)).fetchone()[0]
        top = w.conn.execute("SELECT MAX(seq) FROM entry_number WHERE allocation_id=?", (aid,)).fetchone()[0]
        w.conn.execute("UPDATE entry_number SET status='AVAILABLE', allocation_id=NULL, order_id=NULL, line_item_id=NULL, allocation_seq=0, "
                       "allocated_at=NULL WHERE allocation_id=? AND seq=?", (aid, top))
        w.conn.execute("DELETE FROM entry_event WHERE allocation_id=? AND seq=?", (aid, top))
        w.conn.execute("UPDATE allocation SET held_count=2, target_count=2 WHERE allocation_id=?", (aid,))
        r = loadrecon.reconcile(w.inputs())
        self.assertIn("d1.exact_quantity_per_order", failed(r))
        self.assertIn("FAIL", w.sql().values())

    def test_R32_out_of_range_allocation_fails(self):
        w = World750()
        e = w.conn.execute("SELECT allocation_id, order_id, line_item_id FROM entry_number WHERE competition_id=? AND seq=2650", (L1_CID,)).fetchone()
        w.conn.execute("UPDATE entry_number SET status='AVAILABLE', allocation_id=NULL, order_id=NULL, line_item_id=NULL, allocation_seq=0, "
                       "allocated_at=NULL WHERE competition_id=? AND seq=2650", (L1_CID,))
        w.conn.execute("UPDATE entry_number SET status='ALLOCATED', allocation_id=?, order_id=?, line_item_id=?, allocation_seq=1, "
                       "allocated_at='x' WHERE competition_id=? AND seq=2700", (e[0], e[1], e[2], L1_CID))
        w.conn.execute("UPDATE entry_event SET seq=2700, entry_number='QAL2700' WHERE competition_id=? AND seq=2650", (L1_CID,))
        r = loadrecon.reconcile(w.inputs())
        self.assertIn("d1.allocated_exact_range", failed(r))
        self.assertIn("d1.no_out_of_range", failed(r))
        self.assertEqual(w.sql()["allocated_range"], "FAIL")

    def test_R33_duplicate_allocation_sequence_fails(self):
        w = World750()
        e = w.conn.execute("SELECT * FROM entry_event WHERE competition_id=? AND seq=1500", (L1_CID,)).fetchone()
        w.conn.execute("INSERT INTO entry_event (occurred_at, competition_id, seq, entry_number, allocation_id, allocation_seq, event_type, "
                       "from_status, to_status, order_id, customer_ref, reason, actor, run_id, webhook_id, detail_json) "
                       "SELECT occurred_at, competition_id, seq, entry_number, allocation_id, allocation_seq, event_type, from_status, to_status, "
                       "order_id, customer_ref, reason, actor, run_id, webhook_id, detail_json FROM entry_event WHERE id=?", (e[0],))
        r = loadrecon.reconcile(w.inputs())
        self.assertIn("events.one_per_issue", failed(r))
        self.assertIn("events.count_equals_units", failed(r))
        self.assertEqual(w.sql()["duplicate_issue"], "FAIL")
        w2 = World750()
        w2.conn.execute("UPDATE entry_number SET allocation_seq=2 WHERE competition_id=? AND seq=1200", (L1_CID,))
        r2 = loadrecon.reconcile(w2.inputs())
        self.assertIn("d1.issued_once", failed(r2))
        self.assertEqual(w2.sql()["issued_once"], "FAIL")

    def test_R34_untouched_rows_changing_fails(self):
        w = World750()
        w.conn.execute("UPDATE entry_number SET status='ALLOCATED', allocation_id='x', order_id='1', line_item_id='1', allocation_seq=1, "
                       "allocated_at='x' WHERE competition_id='15897614614902' AND seq=1002")
        r = loadrecon.reconcile(w.inputs())
        self.assertIn("untouched.entries", failed(r))
        w2 = World750()
        w2.conn.execute("UPDATE competition SET title_snapshot='changed' WHERE competition_id='15897614614902'")
        self.assertIn("untouched.competitions", failed(loadrecon.reconcile(w2.inputs())))
        w3 = World750()
        w3.conn.execute("UPDATE allocation SET updated_at='changed' WHERE competition_id='15897614614902'")
        self.assertIn("untouched.allocations", failed(loadrecon.reconcile(w3.inputs())))

    def test_R35_zero_value_validation(self):
        w = World750()
        inp = w.inputs()
        r = loadrecon.reconcile(inp)
        self.assertNotIn("shopify.zero_value", failed(r))
        self.assertNotIn("d1.zero_value_lines", failed(r))
        self.assertEqual(w.sql()["zero_value"], "PASS")
        inp["orders"][3]["totalPriceSet"]["shopMoney"]["amount"] = "1.00"
        self.assertIn("shopify.zero_value", failed(loadrecon.reconcile(inp)))
        w.conn.execute("UPDATE allocation SET line_total_minor=100 WHERE order_id=?", (oid(w.rows[0]["index"]),))
        self.assertIn("d1.zero_value_lines", failed(loadrecon.reconcile(w.inputs())))
        self.assertEqual(w.sql()["zero_value"], "FAIL")

    def test_shopify_side_failures(self):
        w = World750()
        inp = w.inputs()
        inp["orders"][0]["displayFinancialStatus"] = "PENDING"
        self.assertIn("shopify.paid_not_test_not_cancelled", failed(loadrecon.reconcile(inp)))
        inp = w.inputs()
        inp["orders"].append(dict(inp["orders"][0], id="gid://shopify/Order/1", name="#9999"))
        self.assertIn("shopify.orders_bind_to_plan", failed(loadrecon.reconcile(inp)))
        inp = w.inputs()
        inp["drafts"][0]["status"] = "OPEN"
        self.assertIn("shopify.draft_statuses", failed(loadrecon.reconcile(inp)))
        inp = w.inputs()
        inp["product"]["inventoryQuantity"] = 5
        self.assertIn("shopify.stock_matches_units", failed(loadrecon.reconcile(inp)))
        inp = w.inputs()
        inp["wl"] = inp["wl"][1:]
        self.assertIn("events.attributed_to_wl_invocations", failed(loadrecon.reconcile(inp)))

    def test_sql_render_is_literal_and_read_only(self):
        sys.path.insert(0, os.path.join(REPO, "tools", "gs"))
        import gs
        for name, sql in loadrecon.sql_checks():
            r = loadrecon.render(sql, loadrecon.params_for(L1_CID, 1650, 750))
            self.assertNotIn(":cid", r)
            ok, why = gs.read_only_sql(r)
            self.assertTrue(ok, f"{name}: {why}")
        with self.assertRaises(ValueError):
            loadrecon.render("SELECT :cid", {"cid": "1' OR '1"})


# =================================================================================================================================
CANARY_DRAFT = {"id": "gid://shopify/DraftOrder/1614955151734", "name": "#D36", "status": "COMPLETED", "tags": ["qa-load", "QAL-CANARY"]}


def canary_order(gid="gid://shopify/Order/13599260639606", name="#1029", tags=("qa-load", "QAL-CANARY")):
    return {"id": gid, "name": name, "test": False, "cancelledAt": None, "displayFinancialStatus": "PAID", "tags": list(tags),
            "totalPriceSet": {"shopMoney": {"amount": "0.0"}}, "totalTaxSet": {"shopMoney": {"amount": "0.0"}},
            "lineItems": {"nodes": [{"id": "gid://shopify/LineItem/1", "quantity": 1, "product": {"id": "gid://shopify/Product/15916653642102"}}]}}


def listing_handler(qal, qaload, created=None):
    """Fake Stress Driver: the two-list draft precheck, then draftOrderCreate."""
    n = {"k": 0}

    def h(body):
        if "qaload:" in body["query"]:
            return 200, {}, {"data": {"qal": {"nodes": list(qal)}, "qaload": {"nodes": list(qaload)}}}
        n["k"] += 1
        if created is not None:
            created.append(body)
        return 200, {"x-request-id": f"q{n['k']}"}, {"data": {"draftOrderCreate": {"draftOrder": {
            "id": f"gid://shopify/DraftOrder/{77000 + n['k']}", "name": f"#D{n['k']}", "status": "OPEN",
            "tags": body["variables"]["input"]["tags"], "totalPriceSet": {"shopMoney": {"amount": "0.0"}}}, "userErrors": []}}}
    return h


class CanaryExclusionTests(unittest.TestCase):
    """Stage 2 follow-up (6 Oct 2026): the known canary (draft 1614955151734, order 13599260639606 / #1029) is excluded by exact ID."""

    def test_C00_single_explicit_record(self):
        self.assertEqual(common.KNOWN_CANARY["draft_gid"], "gid://shopify/DraftOrder/1614955151734")
        self.assertEqual(common.KNOWN_CANARY["order_gid"], "gid://shopify/Order/13599260639606")
        # exact GID only: no tag, name, bare number or prefix matches
        for x in ("QAL-CANARY", "qa-load", "#1029", "13599260639606", "gid://shopify/Order/135992606396", None,
                  "gid://shopify/Order/13599260639606 ", "gid://shopify/DraftOrder/13599260639606"):
            self.assertFalse(common.is_known_canary_order(x), x)
        for x in ("QAL-CANARY", "#D36", "1614955151734", "gid://shopify/Order/1614955151734", None):
            self.assertFalse(common.is_known_canary_draft(x), x)

    def test_C01_canary_does_not_block_staging(self):
        d = tmpdir()
        c = shop.Client("stress_driver", {stage.DRAFT_CREATE, stage.LIST_QAL}, ENV,
                        FakeTx(listing_handler([CANARY_DRAFT], [CANARY_DRAFT]), "write_draft_orders"))
        r = stage.stage(plan_template(), good_product(), c, FakeClock(), d, True, CONFIRM_STAGE)
        self.assertEqual((r["status"], r["created"]), ("COMPLETE", 750))
        shutil.rmtree(d)

    def test_C01b_unexpected_drafts_still_refuse_staging(self):
        others = [
            ([CANARY_DRAFT, {"id": "gid://shopify/DraftOrder/9", "name": "#D99", "tags": ["QAL"]}], [CANARY_DRAFT]),     # extra QAL
            ([CANARY_DRAFT], [CANARY_DRAFT, {"id": "gid://shopify/DraftOrder/8", "name": "#D98", "tags": ["qa-load"]}]),  # extra qa-load
            ([{"id": "gid://shopify/DraftOrder/7", "name": "#D97", "tags": ["qa-load", "QAL-CANARY"]}], []),              # second canary
        ]
        for qal, qaload in others:
            d = tmpdir()
            created = []
            c = shop.Client("stress_driver", {stage.DRAFT_CREATE, stage.LIST_QAL}, ENV,
                            FakeTx(listing_handler(qal, qaload, created), "write_draft_orders"))
            with self.assertRaises(Refused):
                stage.stage(plan_template(), good_product(), c, FakeClock(), d, True, CONFIRM_STAGE)
            self.assertEqual(created, [], "a draft was created despite an unexpected existing draft")
            shutil.rmtree(d)
        with self.assertRaises(Refused):             # a failed listing is never read as "no drafts"
            stage.existing_load_drafts(200, {"data": {"qal": {"nodes": []}}})

    def test_C02_canary_excluded_from_reconciliation_population(self):
        w = World750()
        inp = w.inputs()
        inp["orders"].append(canary_order())
        inp["drafts"].append({"id": CANARY_DRAFT["id"], "status": "COMPLETED"})
        r = loadrecon.reconcile(inp)
        self.assertEqual(failed(r), [])
        self.assertEqual(r["excluded_known_canary"], ["gid://shopify/Order/13599260639606"])
        bind, probs = shopsnapl.check(inp["orders"], w.plan)
        self.assertEqual((len(bind), probs), (750, []))

    def test_C03_other_non_plan_order_still_fails(self):
        w = World750()
        for extra in (canary_order(gid="gid://shopify/Order/13599260639607", name="#1030", tags=("qa-load",)),
                      canary_order(gid="gid://shopify/Order/1", name="#1029")):           # same name, different id: not excluded
            inp = w.inputs()
            inp["orders"] += [canary_order(), extra]
            r = loadrecon.reconcile(inp)
            self.assertIn("shopify.orders_bind_to_plan", failed(r))
            self.assertEqual(r["result"], "FAIL")
            _, probs = shopsnapl.check(inp["orders"], w.plan)
            self.assertTrue(probs)

    def test_C04_second_canary_like_order_not_ignored(self):
        w = World750()
        inp = w.inputs()
        inp["orders"] += [canary_order(), canary_order(gid="gid://shopify/Order/13599260640000", name="#1031")]
        r = loadrecon.reconcile(inp)
        self.assertIn("shopify.orders_bind_to_plan", failed(r))
        self.assertEqual(r["excluded_known_canary"], ["gid://shopify/Order/13599260639606"])
        _, probs = shopsnapl.check(inp["orders"], w.plan)
        self.assertEqual(len(probs), 1)
        inp = w.inputs()                              # a second canary-like DRAFT is an unexpected draft
        inp["drafts"] += [{"id": CANARY_DRAFT["id"], "status": "COMPLETED"}, {"id": "gid://shopify/DraftOrder/7", "status": "COMPLETED"}]
        self.assertIn("shopify.no_unexpected_drafts", failed(loadrecon.reconcile(inp)))
        with self.assertRaises(Refused):              # and canary.py refuses to create a second canary
            canary.run_canary(good_canary(), [], L1_CID, None, lambda: "true", tmpdir(), CONFIRM_CANARY, FakeClock())

    def test_C05_expected_population_stays_750(self):
        w = World750()
        inp = w.inputs()
        inp["orders"].append(canary_order())
        r = loadrecon.reconcile(inp)
        self.assertEqual((r["expected_orders"], r["expected_units"]), (750, 1650))
        self.assertEqual(r["stage_counts"]["shopify_orders"], 750)
        self.assertEqual(len(plan_template()["rows"]), 750)
        self.assertNotIn(common.KNOWN_CANARY["draft_gid"], {x["draft_id"] for x in bound_plan()["rows"]})

    def test_C06_canary_cannot_contribute_to_qal_counts(self):
        w = World750()
        w._alloc(L1_CID, "13599260639606", "#1029", "1", 1, "QAL", [2651], "2026-10-06T14:00:00.000Z")
        inp = w.inputs()
        inp["orders"].append(canary_order())
        r = loadrecon.reconcile(inp)
        for c in ("d1.known_canary_has_no_allocation", "d1.pool_counts", "d1.allocation_set_equals_orders",
                  "events.count_equals_units"):
            self.assertIn(c, failed(r))
        sq = w.sql()
        self.assertEqual(sq["allocation_count"], "FAIL")
        self.assertEqual(sq["units_sum"], "FAIL")
        # a canary carrying a plan tag is still excluded, so it can never stand in for a plan order
        w2 = World750()
        inp2 = w2.inputs()
        planted = canary_order(tags=("qa-load", "QAL", w2.rows[0]["tag"]))
        inp2["orders"] = [o for o in inp2["orders"] if w2.rows[0]["tag"] not in o["tags"]] + [planted]
        self.assertIn("shopify.orders_bind_to_plan", failed(loadrecon.reconcile(inp2)))

    def test_C07_safety_and_allowlist_unchanged(self):
        # the guard's D1 allow-list knows nothing about the canary: an allocation for it is a SAFETY deviation as before
        w = World750(n_orders=3)
        w._alloc(L1_CID, "13599260639606", "#1029", "1", 1, "QAL", [1010], "x")
        dev = guardl.allowlist(w.pre, snapshot(w.conn), L1_CID, [r["qty"] for r in w.rows], 1028)
        self.assertTrue(any("not explained by a completed plan draft" in x for x in dev))
        self.assertTrue(guardl.Guard().tick(1.0, {"allowlist": {"ts": 1.0, "deviations": dev}, "worker": {"dry_run": "false"},
                                                  "driver": {"started": 0.0}})["safety"])
        # and the load driver never accepts the canary draft as a plan draft
        self.assertFalse(common.allowed_draft(bound_plan(), common.KNOWN_CANARY["draft_gid"]))


# =================================================================================================================================
class RegisterTests(unittest.TestCase):
    def test_H6_chunks_under_limit_and_exact_2000(self):
        files = regsql.build(L1_CID, "2026-10-06T10:00:00.000Z")
        self.assertEqual(files[0][0], "00-competition.sql")
        self.assertEqual(files[-1][0], "99-open.sql")
        pool = [s for n, s in files if n.endswith("-pool.sql")]
        self.assertGreaterEqual(len(pool), 2, "2000 rows cannot fit one 90 KB statement")
        for n, s in files:
            self.assertLess(len(s.encode()), regsql.MAX_BYTES + 1, n)
            self.assertLess(len(s.encode()), regsql.D1_STATEMENT_LIMIT, n)
        conn = sqlite3.connect(":memory:")
        for m in migrations():
            conn.executescript(m)
        for n, s in files:
            conn.executescript(s)
        rows = conn.execute("SELECT seq, entry_number, status, allocation_seq FROM entry_number WHERE competition_id=? ORDER BY seq", (L1_CID,)).fetchall()
        self.assertEqual(len(rows), 2000)
        self.assertEqual([r[0] for r in rows], list(range(1001, 3001)))
        self.assertEqual({r[2] for r in rows}, {"AVAILABLE"})
        self.assertEqual({r[3] for r in rows}, {0})
        self.assertEqual((rows[0][1], rows[-1][1]), ("QAL1001", "QAL3000"))
        comp = conn.execute("SELECT status, capacity, prefix, start_number, pad_width FROM competition WHERE competition_id=?", (L1_CID,)).fetchone()
        self.assertEqual(comp, ("OPEN", 2000, "QAL", 1001, 4))

    def test_H6_refusals(self):
        with self.assertRaises(Refused):
            regsql.build("abc", "2026-10-06T10:00:00.000Z")
        with self.assertRaises(Refused):
            regsql.build(L1_CID, "yesterday")
        r = subprocess.run(["bash", os.path.join(L1, "register.sh"), L1_CID, "/nonexistent", "/tmp/x"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("REFUSED", r.stdout)


# =================================================================================================================================
GATE_VER = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"


def good_worker(now, **kw):
    """A fresh, good livewin.py QA Worker reading during the live window."""
    w = {"ts": now, "read_ok": True, "production_present": False, "dry_run": "false", "versions": [[GATE_VER, 100]],
         "expected_version": GATE_VER, "http": {"production": 404, "settings": 200, "deployments": 200}, "problems": []}
    w.update(kw)
    return w


def wl_win(lo_ms, hi_ms, events=(), complete=True, **kw):
    w = {"ts": hi_ms / 1000 + 60, "from_ms": lo_ms, "to_ms": hi_ms, "complete": complete, "events": list(events), "problems": []}
    w.update(kw)
    return w


class GuardTests(unittest.TestCase):
    def base(self, now, **kw):
        d = {"sampler": {"ts": now, "subs_ts": now, "subs_ok": True}, "d1_count": {"ts": now, "allocated": kw.pop("allocated", 0)},
             "worker": good_worker(now), "wl": [], "wl_from_ms": int(now * 1000),
             "driver": {"started": 0.0, "done": False, "last_sent_ts": None, "completed_units": 0}}
        d.update(kw)
        return d

    def test_safety_sources(self):
        cases = [dict(sampler={"ts": 1, "subs_ts": 1, "subs_ok": False}), dict(manual_stop=True), dict(load_safety_file=True),
                 dict(worker=good_worker(10.0, production_present=True)), dict(worker=good_worker(10.0, dry_run="true")),
                 dict(allowlist={"ts": 1, "deviations": ["new allocation on competition 15897614614902 (not L1)"]}),
                 dict(events=[ev(1, status=500), ev(2, status=500)])]
        for c in cases:
            g = guardl.Guard()
            out = g.tick(10.0, self.base(10.0, **c))
            self.assertTrue(out["safety"], c)
            self.assertEqual(out["actions"], ["RESTORE_DRY_RUN"], c)

    def test_R20_non_plan_allocation_is_safety(self):
        w = World750(n_orders=10)
        post = snapshot(w.conn)
        qtys = [r["qty"] for r in w.rows]
        self.assertEqual(guardl.allowlist(w.pre, post, L1_CID, qtys, 1028), [])
        # an allocation the driver did not complete (one fewer completed quantity than allocations)
        dev = guardl.allowlist(w.pre, post, L1_CID, qtys[1:], 1028)
        self.assertTrue(any("not explained by a completed plan draft" in x for x in dev))
        # an allocation on another competition
        w._alloc("15897614614902", "13500000000099", "#1099", "36000000000099", 1, "QAE", [1002], "x")
        dev = guardl.allowlist(w.pre, snapshot(w.conn), L1_CID, qtys, 1028)
        self.assertTrue(any("not L1" in x for x in dev))
        g = guardl.Guard()
        self.assertTrue(g.tick(1.0, self.base(1.0, allowlist={"ts": 1.0, "deviations": dev}))["safety"])
        # an allocation for a pre-existing order number
        w2 = World750(n_orders=3)
        self.assertTrue(any("pre-existing order" in x for x in guardl.allowlist(w2.pre, snapshot(w2.conn), L1_CID, [r["qty"] for r in w2.rows], 5000)))

    def test_load_reasons(self):
        g = guardl.Guard()
        out = g.tick(10.0, self.base(10.0, events=[ev(1, status=500)]))
        self.assertTrue(out["load"])
        self.assertEqual(out["actions"], [])
        g = guardl.Guard()
        out = g.tick(200.0, {"driver": {"started": 0.0, "done": False}, "worker": {"dry_run": "false"}})
        self.assertTrue(any("stale" in x for x in out["load"]))
        self.assertTrue(any("D1 count overdue" in x for x in out["load"]))

    def test_drain_complete_deadline_and_no_progress(self):
        g = guardl.Guard()
        g.tick(100.0, self.base(100.0, allocated=1000, driver={"started": 0.0, "done": True, "last_sent_ts": 90.0, "completed_units": 1650}))
        out = g.tick(130.0, self.base(130.0, allocated=1650, driver={"started": 0.0, "done": True, "last_sent_ts": 90.0, "completed_units": 1650}))
        self.assertEqual((out["verdict"], out["drained"], out["actions"]), ("DRAINED", True, ["RESTORE_DRY_RUN"]))
        g = guardl.Guard()
        drv = {"started": 0.0, "done": True, "last_sent_ts": 0.0, "completed_units": 1650}
        for t in range(0, 1860, 30):
            out = g.tick(float(t), self.base(float(t), allocated=1000 + t // 3, driver=drv))
        self.assertEqual(out["verdict"], "DRAIN_DEADLINE")
        g = guardl.Guard()
        for t in range(0, 700, 30):
            out = g.tick(float(t), self.base(float(t), allocated=1200, driver=drv))
        self.assertEqual(out["verdict"], "NO_PROGRESS")
        self.assertEqual(out["actions"], ["RESTORE_DRY_RUN"])

    def test_restore_fail_safe_requested_until_verified(self):
        g = guardl.Guard()
        g.tick(1.0, self.base(1.0, manual_stop=True))
        for t in range(2, 30):
            self.assertEqual(g.tick(float(t), self.base(float(t)))["actions"], ["RESTORE_DRY_RUN"])
        out = g.tick(31.0, self.base(31.0, worker={"dry_run": "true", "restored_verified": True}))
        self.assertEqual(out["actions"], [])
        self.assertTrue(out["restore_verified"])

    def test_guardl_sh_fail_safe(self):
        d = tmpdir()
        stub = os.path.join(d, "guardl.py")
        common.write_json(os.path.join(d, "guardl-config.json"), {"restore_cmd": f"echo restored >> {d}/restores",
                                                                  "verify_cmd": "echo true"})
        # a guardl.py that crashes on every tick: the loop must restore anyway and exit only after verification
        src = open(os.path.join(L1, "guardl.sh")).read().replace('H="$(cd "$(dirname "$0")" && pwd)"', f'H="{d}"')
        with open(os.path.join(d, "guardl.sh"), "w") as f:
            f.write(src)
        with open(stub, "w") as f:
            f.write("import sys\nsys.exit(1 if 'tick' in sys.argv else 0)\n")
        r = subprocess.run(["timeout", "20", "bash", os.path.join(d, "guardl.sh"), d], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("restored", open(os.path.join(d, "restores")).read())
        self.assertIn("fail-safe -> restore", open(os.path.join(d, "guardl.log")).read())
        shutil.rmtree(d)

    def test_guardl_sh_retries_failed_restore(self):
        d = tmpdir()
        common.write_json(os.path.join(d, "guardl-config.json"), {
            "restore_cmd": f"n=$(cat {d}/n 2>/dev/null || echo 0); echo $((n+1)) > {d}/n; [ $n -ge 1 ]", "verify_cmd": "echo true"})
        src = open(os.path.join(L1, "guardl.sh")).read().replace('H="$(cd "$(dirname "$0")" && pwd)"', f'H="{d}"').replace("-ge 10 ]", "-ge 1 ]")
        with open(os.path.join(d, "guardl.sh"), "w") as f:
            f.write(src)
        with open(os.path.join(d, "guardl.py"), "w") as f:
            f.write("import sys\nsys.exit(10 if 'tick' in sys.argv else 0)\n")
        r = subprocess.run(["timeout", "30", "bash", os.path.join(d, "guardl.sh"), d], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(d, "RESTORE-ALARM")), "the failed first restore must raise the alarm")
        self.assertGreaterEqual(int(open(os.path.join(d, "n")).read()), 2)
        shutil.rmtree(d)

    def test_tail_parsing(self):
        o = {"eventTimestamp": 1000000, "wallTime": 900, "outcome": "ok",
             "event": {"request": {"url": "https://x.dev/webhooks/orders/paid", "headers": {"x-shopify-topic": "orders/paid",
                       "x-shopify-webhook-id": "W", "cf-ray": "R"}}, "response": {"status": 200}},
             "logs": [{"message": [json.dumps({"event": "shopify_request_retry", "error_kind": "THROTTLED"})]},
                      {"message": [json.dumps({"event": "webhook_processed", "order_gid": "gid://shopify/Order/5", "lines": 1})]}]}
        e, thr = guardl.tail_events(o)
        self.assertEqual((e["topic"], e["status"], e["webhook_id"], e["processed"], e["order"]), ("orders/paid", 200, "W", True, "gid://shopify/Order/5"))
        self.assertEqual(thr, [1000.0])
        o2 = dict(o, outcome="exception")
        self.assertIsNone(guardl.tail_events(o2)[0]["status"])
        self.assertEqual(len(guardl.tail_objects("noise " + json.dumps(o) + "\n" + json.dumps(o))), 2)


# =================================================================================================================================
class ShopsnapTests(unittest.TestCase):
    def test_refuses_during_window_and_paginates(self):
        d = tmpdir()
        w = World750()
        orders = w.inputs()["orders"]

        def h(body):
            after = body["variables"]["after"]
            i = int(after) if after else 0
            page = orders[i:i + 100]
            return 200, {}, {"data": {"orders": {"nodes": page, "pageInfo": {"hasNextPage": i + 100 < len(orders), "endCursor": str(i + 100)}}}}
        tx = FakeTx(h)
        c = shop.Client("allocator", {shopsnapl.ORDERS_DOC}, ENV, tx)
        open(os.path.join(d, f"load-{L1_CID}.done"), "w").close()
        with self.assertRaises(Refused):
            shopsnapl.export(c, d, L1_CID)
        self.assertEqual(tx.sent, [], "an allocator-app order read happened inside the load window")
        common.write_json(os.path.join(d, "guardl.json"), {"restore_verified": True})
        r = shopsnapl.export(c, d, L1_CID)
        self.assertEqual((r["pages"], len(r["orders"])), (8, 750))
        bind, probs = shopsnapl.check(r["orders"], w.plan)
        self.assertEqual((len(bind), probs), (750, []))
        bad = json.loads(json.dumps(r["orders"]))
        bad[0]["tags"] = ["qa-load"]
        bad[1]["lineItems"]["nodes"][0]["quantity"] = 99
        bad.append(dict(bad[2]))
        _, probs = shopsnapl.check(bad, w.plan)
        self.assertEqual(len(probs), 3)
        shutil.rmtree(d)


# =================================================================================================================================
class ObservabilityTests(unittest.TestCase):
    def test_R26_workers_logs_20000_records_page(self):
        sys.path.insert(0, os.path.join(REPO, "qa", "b3-stress-harness", "b3obs"))
        import importlib
        obsq = importlib.import_module("obsq")
        recs, t0 = [], 1_800_000_000_000
        for i in range(10_000):
            ts = t0 + i * 10
            rid = f"req{i:05d}"
            status = 500 if i == 4321 else 200
            recs.append({"timestamp": ts + 900, "$metadata": {"id": f"i{i:05d}", "service": obsq.SERVICE, "type": "cf-worker-event", "requestId": rid},
                         "$workers": {"wallTimeMs": 900, "cpuTimeMs": 5, "outcome": "ok", "scriptName": obsq.SERVICE, "scriptVersion": {"id": "v"},
                                      "eventType": "fetch", "event": {"request": {"url": "https://x.dev/webhooks/orders/paid",
                                      "headers": {"x-shopify-topic": "orders/paid", "x-shopify-webhook-id": f"W{i}", "cf-ray": f"ray{i}"}},
                                      "response": {"status": status}}}})
            recs.append({"timestamp": ts + 500, "$metadata": {"id": f"l{i:05d}", "service": obsq.SERVICE, "type": "cf-worker", "requestId": rid},
                         "source": {"event": "webhook_processed", "order_gid": f"gid://shopify/Order/{i}", "lines": 1}, "$workers": {}})
        d = tmpdir()
        fake = os.path.join(d, "fake.json")
        json.dump({"records": recs}, open(fake, "w"))
        os.environ["OBSQ_FAKE"] = fake
        try:
            obsq._fake_calls[0] = 0
            r = obsq.fetch_window(t0, t0 + 200_000)
        finally:
            del os.environ["OBSQ_FAKE"]
        self.assertEqual(r["problems"], [])
        self.assertTrue(r["complete"])
        self.assertEqual(r["records"], 20_000)
        self.assertEqual(r["pages"], 11)              # 10 full pages of 2000 + the short (empty) page
        self.assertEqual(len(r["invocations"]), 10_000)
        w = guardl.wl_from_obsq(r)
        self.assertEqual(len(w["events"]), 10_000)
        f = failwatch.FailWatch()
        f.confirm_wl(w["events"], w["complete"])
        self.assertEqual(f.state()["total"], {"orders/paid": 1})
        self.assertEqual(f.level, "LOAD_STOP")
        shutil.rmtree(d)

    def test_R27_2000_plus_invocations_concurrency(self):
        rnd = random.Random(3)
        iv = []
        for _ in range(2500):
            s = rnd.uniform(0, 600)
            iv.append((s, s + rnd.uniform(0.05, 4.0)))
        brute = 0
        pts = sorted({s for s, _ in iv})
        for p in pts:
            brute = max(brute, sum(1 for s, e in iv if s <= p < e))
        self.assertEqual(loadmetrics.peak_concurrency(iv), brute)
        self.assertEqual(loadmetrics.peak_concurrency([(0, 1), (1, 2)]), 1, "touching intervals do not overlap")
        f = failwatch.FailWatch()
        for i, (s, e) in enumerate(iv):
            f.observe({"ts": s, "topic": "orders/paid" if i % 2 else "orders/create", "webhook_id": f"w{i}", "status": 200,
                       "wall_ms": (e - s) * 1000, "request_id": f"r{i}"})
        self.assertEqual(f.level, "NONE")
        self.assertEqual(sum(f.state()["successes"].values()), 2500)

    def test_metrics_labels_and_values(self):
        r = sim_run(36.0)
        w = r["world"]
        inp = {"arrivals": [t for t, _ in w.created], "completions": {str(i): t for t, i in w.completed},
               "sampler": [{"ts": t, "available": v, "maximum": 2000, "actual": 0} for t, v in w.samples],
               "throttled": w.throttled, "governor": r["gov"].summary(),
               "invocations": [{"start": t - 0.9, "end": t, "topic": "orders/paid", "status": 200} for t, _ in w.completed]}
        m = loadmetrics.compute(inp)
        for k, v in m.items():
            self.assertIn(v["label"], ("OBSERVED", "DERIVED"), k)
        self.assertEqual(m["theoretical_capacity_orders_per_s"]["label"], "DERIVED")
        self.assertEqual(m["points_per_order"]["label"], "DERIVED")
        self.assertEqual(m["observed_sustainable_throughput"]["label"], "OBSERVED")
        self.assertAlmostEqual(m["points_per_order"]["value"], 36.0, delta=3.0)
        self.assertAlmostEqual(m["theoretical_capacity_orders_per_s"]["value"], 100 / 36, delta=0.25)
        self.assertEqual(m["observed_sustainable_throughput"]["value"], 2.5)
        self.assertEqual(m["stability_point"]["value"]["low"], 2.5)
        self.assertEqual(m["stability_point"]["value"]["high"], 3.0)
        for w_ in (10, 30, 60):
            self.assertIn(f"allocation_rate_{w_}s_peak", m)
        self.assertTrue(m["backlog_growth_above_stability"]["value"])
        self.assertLess(m["backlog_growth_above_stability"]["value"][0]["bucket_slope_pts_per_s"], -3)
        self.assertGreaterEqual(m["peak_worker_concurrency"]["value"], 1)

    def test_rolling_windows(self):
        s = [1] * 30 + [0] * 30
        self.assertEqual(loadmetrics.rolling(s, 10)[29], 1.0)
        self.assertEqual(loadmetrics.rolling(s, 10)[45], 0.0)
        self.assertAlmostEqual(loadmetrics.rolling(s, 60)[59], 0.5)


# =================================================================================================================================
class ManifestRehearsalTests(unittest.TestCase):
    def test_R36_manifest_deterministic(self):
        d = tmpdir()
        os.makedirs(os.path.join(d, "sub"))
        for n, c in (("b.json", "{}"), ("a.txt", "x"), ("sub/Z.log", "z"), ("sub/a.log", "q")):
            open(os.path.join(d, n), "w").write(c)
        t1 = manifest.write(d)
        t2 = manifest.build(d)
        self.assertEqual(t1, t2)
        self.assertEqual([l.split("  ")[1] for l in t1.splitlines()], ["a.txt", "b.json", "sub/Z.log", "sub/a.log"])
        self.assertTrue(manifest.verify(d))
        r = subprocess.run(["sha256sum", "-c", "--quiet", "MANIFEST.sha256"], cwd=d, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        open(os.path.join(d, "a.txt"), "w").write("y")
        self.assertFalse(manifest.verify(d))
        shutil.rmtree(d)

    def test_H10_rehearsal_refusals_and_zero_mutations(self):
        d = tmpdir()

        def h(body):
            return 200, {"x-request-id": "r"}, {"data": {"draftOrder": {"id": "x", "status": "OPEN"}}}
        tx = FakeTx(h, "write_draft_orders")
        c = shop.Client("stress_driver", {rehearse.PROBE}, ENV, tx)
        with self.assertRaises(Refused):
            rehearse.proxy(c, ["gid://shopify/DraftOrder/1"], FakeClock(), d, "no")
        s = rehearse.proxy(c, ["gid://shopify/DraftOrder/1"], FakeClock(), d, CONFIRM_REHEARSE, rates=(1.0, 5.0), step_s=4.0)
        self.assertEqual(s["1.0"]["n"], 4)
        self.assertEqual(s["5.0"]["n"], 20)
        self.assertTrue(all("mutation" not in b["query"] for b in tx.sent))
        self.assertTrue(shop.read_only_graphql(rehearse.PROBE)[0])
        ca = shop.Client("allocator", set(sampler.DOCUMENTS) | {clampmod.CLAMP_DOC}, ENV, FakeTx(lambda b: (200, {}, cost(1500))))
        with self.assertRaises(Refused):
            rehearse.clamp_rehearsal(ca, FakeClock(), d, CONFIRM_REHEARSE, APPROVE_T, lambda: "false")
        with self.assertRaises(Refused):
            rehearse.clamp_rehearsal(ca, FakeClock(), d, CONFIRM_REHEARSE, None, lambda: "true")
        self.assertEqual(rehearse.main(["proxy", "--drafts", "x", "--out", d]), 2)
        shutil.rmtree(d)

    def test_simulate_cli_offline(self):
        d = tmpdir()
        out = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(out):
            rc = load.main(["simulate", "--out", d, "--cost", "36"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["completed"], 750)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(load.main(["plan-template", "--out", os.path.join(d, "p.json")]), 0)
        self.assertEqual(json.load(open(os.path.join(d, "p.json"))), json.loads(common.canonical(plan_template())))
        shutil.rmtree(d)



# =================================================================================================================================
# Stage 4 wiring (offline). QA Worker and Workers Logs inputs fail closed; livewin.py writes the state files load.py / guardl /
# fire.py read; window.sh sequences the existing B3 gate/restore mechanisms. No network: every transport is a fake.
import livewin  # noqa: E402

B3D = os.path.join(REPO, "qa", "b3-stress-harness", "b3stress")
FIRE = shop.load_fire()


class FakeCF:
    """Fake Cloudflare transport for the REAL fire.cf_json: records every (method, path); answers by path suffix."""

    def __init__(self, prod=(404, {"success": False, "errors": [{"code": 10007}]}), dry=("false",), versions=((GATE_VER, 100),),
                 settings_status=200, deployments_status=200, fail=None):
        self.prod, self.dry, self.versions = prod, dry, versions
        self.settings_status, self.deployments_status, self.fail, self.calls = settings_status, deployments_status, fail, []

    def open(self):
        return FakeConn(self)

    def handle(self, method, path, body, headers):
        self.calls.append((method, path))
        if self.fail and self.fail in path:
            raise ConnectionError("simulated transport failure")
        if path.endswith(f"/{FIRE.PROD_WORKER}/settings"):
            st, b = self.prod
        elif path.endswith(f"/{FIRE.QA_WORKER}/settings"):
            st, b = self.settings_status, {"success": True, "result": {"bindings": [{"name": "DRY_RUN", "type": "plain_text", "text": t}
                                                                                    for t in self.dry] + [{"name": "SHOPIFY_STORE", "type": "plain_text", "text": "x"}]}}
        elif path.endswith(f"/{FIRE.QA_WORKER}/deployments"):
            st, b = self.deployments_status, {"success": True, "result": {"deployments": [
                {"versions": [{"version_id": v, "percentage": pc} for v, pc in self.versions]}]}}
        else:
            st, b = 500, {}
        return st, {}, json.dumps(b).encode()


CF_ENV = {"CLOUDFLARE_ACCOUNT_ID": "acct-test", "CLOUDFLARE_API_TOKEN": "cftok-SECRET-eeeeee"}


def read_worker(cf, exp=GATE_VER, now=100.0, env=CF_ENV):
    return livewin.worker_reading(cf, env, now, exp, FIRE)


class StageFourWorkerTests(unittest.TestCase):
    """The QA Worker input: missing, failed, stale or wrong -> SAFETY (restore), never a silent pass."""

    def tick(self, g, now, **kw):
        return g.tick(now, GuardTests.base(None, now, **kw))

    def test_W01_missing_worker_input_is_safety(self):
        g = guardl.Guard()
        for t in range(100, 131, 5):
            inp = GuardTests.base(None, float(t)); inp.pop("worker")
            out = g.tick(float(t), inp)
            self.assertEqual(out["safety"], [], f"tolerated up to {guardl.WORKER_STALE_S}s (t={t})")
        inp = GuardTests.base(None, 131.0); inp.pop("worker")
        out = g.tick(131.0, inp)
        self.assertTrue(any("not verified" in x for x in out["safety"]), out)
        self.assertEqual(out["actions"], ["RESTORE_DRY_RUN"])

    def test_W02_production_worker_present_is_safety(self):
        for w in (good_worker(10.0, production_present=True), {"production_present": True}, good_worker(10.0, production_present=None)):
            out = guardl.Guard().tick(10.0, GuardTests.base(None, 10.0, worker=w))
            self.assertTrue(any("production Worker" in x for x in out["safety"]), w)
            self.assertEqual(out["actions"], ["RESTORE_DRY_RUN"])
        self.assertTrue(read_worker(FakeCF(prod=(200, {"success": True, "result": {}})))["production_present"])
        self.assertIsNone(read_worker(FakeCF(prod=(500, {})))["production_present"])

    def test_W03_missing_dry_run_is_safety(self):
        out = guardl.Guard().tick(10.0, GuardTests.base(None, 10.0, worker=good_worker(10.0, dry_run=None)))
        self.assertIn("QA Worker DRY_RUN missing", out["safety"])
        r = read_worker(FakeCF(dry=()))
        self.assertTrue(r["read_ok"])
        self.assertIsNone(r["dry_run"])
        out = guardl.Guard().tick(100.0, GuardTests.base(None, 100.0, worker=r))
        self.assertIn("QA Worker DRY_RUN missing", out["safety"])

    def test_W04_unexpected_dry_run_values_are_safety(self):
        for v in ("true", "False", "FALSE", "", "0", "<multiple>"):
            out = guardl.Guard().tick(10.0, GuardTests.base(None, 10.0, worker=good_worker(10.0, dry_run=v)))
            self.assertTrue(any("DRY_RUN" in x for x in out["safety"]), v)
        self.assertEqual(read_worker(FakeCF(dry=("false", "true")))["dry_run"], "<multiple>")

    def test_W04b_dry_run_false_outside_the_window_is_refused(self):
        # before the gate: worker-before refuses a Worker already live
        self.assertTrue(livewin.worker_before_problems(read_worker(FakeCF(dry=("false",)), exp=None)))
        self.assertEqual(livewin.worker_before_problems(read_worker(FakeCF(dry=("true",)), exp=None)), [])
        # after the restore: verify-restore is false while DRY_RUN is still "false", so guardl.sh keeps retrying the restore
        d = tmpdir()
        with open(os.path.join(d, "restorever.txt"), "w") as f:
            f.write(GATE_VER + "\n")
        with open(os.path.join(d, "restoregate.txt"), "w") as f:
            f.write("PASSED\n")
        self.assertFalse(livewin.restore_verified(FakeCF(dry=("false",)), CF_ENV, d, 1.0, FIRE))
        self.assertTrue(livewin.restore_verified(FakeCF(dry=("true",)), CF_ENV, d, 1.0, FIRE))
        with open(os.path.join(d, "restoregate.txt"), "w") as f:
            f.write("FAILED\n")
        self.assertFalse(livewin.restore_verified(FakeCF(dry=("true",)), CF_ENV, d, 1.0, FIRE), "the restore gate must have PASSED")
        shutil.rmtree(d)

    def test_W05_stale_or_failed_readings_are_safety(self):
        g = guardl.Guard()
        out = None
        for t in range(100, 140, 5):
            out = g.tick(float(t), GuardTests.base(None, float(t), worker=good_worker(60.0)))      # a reading 40+ s old
        self.assertTrue(any("not verified" in x for x in out["safety"]))
        g = guardl.Guard()
        for t in range(100, 140, 5):
            out = g.tick(float(t), GuardTests.base(None, float(t), worker=good_worker(float(t), read_ok=False, dry_run=None)))
        self.assertTrue(any("not verified" in x for x in out["safety"]))
        self.assertNotIn("QA Worker DRY_RUN missing", out["safety"], "a failed read is judged by freshness, not by its fields")
        # one failed read between good ones is tolerated
        g = guardl.Guard()
        for t in range(100, 200, 10):
            w = good_worker(float(t), read_ok=False) if t == 150 else good_worker(float(t))
            out = g.tick(float(t), GuardTests.base(None, float(t), worker=w))
        self.assertEqual(out["safety"], [])
        # real readings: an unusable GET or a transport failure is never read_ok
        for cf in (FakeCF(settings_status=500), FakeCF(deployments_status=403), FakeCF(prod=(502, {})), FakeCF(fail="/deployments")):
            self.assertFalse(read_worker(cf)["read_ok"])
        self.assertFalse(read_worker(FakeCF(), env={})["read_ok"])

    def test_W06_wrong_version_is_safety(self):
        for kw in (dict(versions=[["other", 100]]), dict(versions=[[GATE_VER, 50], ["other", 50]]), dict(expected_version=None),
                   dict(versions=None)):
            out = guardl.Guard().tick(10.0, GuardTests.base(None, 10.0, worker=good_worker(10.0, **kw)))
            self.assertTrue(any("gate-passed version" in x for x in out["safety"]), kw)

    def test_W07_good_inputs_stay_clear_for_the_whole_window(self):
        g = guardl.Guard()
        lo = 100_000 - 1
        for t in range(100, 700, 10):
            hi = (t - 60) * 1000
            wins = [wl_win(lo + 1, hi)] if hi > lo and t % 30 == 10 else []
            if wins:
                lo = hi
            out = g.tick(float(t), GuardTests.base(None, float(t), worker=good_worker(float(t)), wl=wins, wl_from_ms=100_000,
                                                   driver={"started": 100.0, "done": False, "last_sent_ts": None, "completed_units": 0}))
            self.assertEqual((out["safety"], out["load"], out["actions"]), ([], [], []), t)

    def test_W08_worker_not_judged_after_restore_requested(self):
        g = guardl.Guard()
        g.tick(10.0, GuardTests.base(None, 10.0, manual_stop=True))
        out = g.tick(20.0, GuardTests.base(None, 20.0, worker=good_worker(20.0, dry_run="true", versions=[["restored", 100]])))
        self.assertEqual(out["safety"], ["manual-stop"])
        out = g.tick(200.0, {"worker": {"restored_verified": True}})
        self.assertEqual(out["actions"], [])

    def test_W09_reading_uses_only_read_only_gets(self):
        cf = FakeCF()
        r = read_worker(cf)
        self.assertTrue(r["read_ok"])
        self.assertEqual({m for m, _ in cf.calls}, {"GET"})
        self.assertEqual(sorted(p.rsplit("/scripts/", 1)[1] for _, p in cf.calls),
                         ["greenside-entry-allocator-qa/deployments", "greenside-entry-allocator-qa/settings", "greenside-entry-allocator/settings"])
        self.assertNotIn("cftok-SECRET", json.dumps(r))


class StageFourWorkersLogsTests(unittest.TestCase):
    """Workers Logs input: failures it reports feed the existing failwatch; missing, gapped, incomplete or stale -> LOAD STOP."""

    def test_L01_missing_workers_logs_is_load_stop(self):
        g = guardl.Guard()
        for t in range(100, 241, 10):
            out = g.tick(float(t), GuardTests.base(None, float(t), wl_from_ms=100_000))
            self.assertEqual(out["load"], [], t)
        out = g.tick(251.0, GuardTests.base(None, 251.0, wl_from_ms=100_000))
        self.assertTrue(any("Workers Logs missing or stale" in x for x in out["load"]), out)
        inp = GuardTests.base(None, 10.0); inp.pop("wl_from_ms")
        self.assertTrue(any("coverage start unknown" in x for x in guardl.Guard().tick(10.0, inp)["load"]))

    def test_L02_failure_seen_only_by_workers_logs_stops(self):
        g = guardl.Guard()
        out = g.tick(200.0, GuardTests.base(None, 200.0, wl_from_ms=100_000, wl=[wl_win(100_000, 140_000, [ev(120, status=500)])]))
        self.assertEqual(out["failwatch"], "LOAD_STOP")
        self.assertTrue(any("failed delivery" in x for x in out["load"]))
        self.assertEqual(out["actions"], [])
        out = g.tick(210.0, GuardTests.base(None, 210.0, wl=[wl_win(140_001, 150_000, [ev(141, status=503)])]))
        self.assertEqual(out["failwatch"], "SAFETY_STOP")
        self.assertEqual(out["actions"], ["RESTORE_DRY_RUN"])
        # a timeout (wall > 5 s) counts as a failed delivery too; tail + Workers Logs copies of one invocation count once
        g = guardl.Guard()
        e = ev(120, status=200, wall=5200, rid="rayX")
        g.tick(200.0, GuardTests.base(None, 200.0, events=[e], wl_from_ms=100_000))
        out = g.tick(201.0, GuardTests.base(None, 201.0, wl=[wl_win(100_000, 140_000, [e])]))
        self.assertEqual((out["failwatch"], g.fw.state()["total"]), ("LOAD_STOP", {"orders/paid": 1}))

    def test_L03_incomplete_window_is_load_stop(self):
        out = guardl.Guard().tick(200.0, GuardTests.base(None, 200.0, wl_from_ms=100_000,
                                                         wl=[wl_win(100_000, 140_000, complete=False, problems=["records 10 != series total 11"])]))
        self.assertTrue(any("not complete" in x for x in out["load"]))

    def test_L04_coverage_gap_is_load_stop(self):
        g = guardl.Guard()
        g.tick(200.0, GuardTests.base(None, 200.0, wl_from_ms=100_000, wl=[wl_win(100_000, 140_000)]))
        out = g.tick(210.0, GuardTests.base(None, 210.0, wl=[wl_win(145_000, 150_000)]))
        self.assertTrue(any("coverage gap" in x for x in out["load"]))
        out = guardl.Guard().tick(200.0, GuardTests.base(None, 200.0, wl_from_ms=100_000, wl=[wl_win(120_000, 140_000)]))
        self.assertTrue(any("coverage gap" in x for x in out["load"]), "coverage must start at wl_from_ms")

    def test_L05_malformed_window_is_load_stop(self):
        for w in ({"complete": True, "events": []}, wl_win(140_000, 100_000), dict(wl_win(1, 2), from_ms="a", to_ms="b")):
            out = guardl.Guard().tick(200.0, GuardTests.base(None, 200.0, wl_from_ms=100_000, wl=[w]))
            self.assertTrue(any("malformed" in x for x in out["load"]), w)

    def test_L06_workers_logs_going_stale_is_load_stop(self):
        g = guardl.Guard()
        g.tick(200.0, GuardTests.base(None, 200.0, wl_from_ms=100_000, wl=[wl_win(100_000, 140_000)]))
        self.assertEqual(g.tick(290.0, GuardTests.base(None, 290.0))["load"], [])
        out = g.tick(291.0, GuardTests.base(None, 291.0))
        self.assertTrue(any("missing or stale" in x for x in out["load"]))

    def test_L07_collect_reads_the_poller_files(self):
        d = tmpdir()
        common.write_json(os.path.join(d, "guardl-config.json"), {"competition_id": L1_CID, "pre_max_order_number": 1029, "repo": REPO,
                                                                  "restore_cmd": "x", "verify_cmd": "y", "wl_from_ms": 100_000})
        with open(os.path.join(d, "worker-poll.jsonl"), "w") as f:
            f.write(json.dumps(good_worker(1.0, dry_run="true")) + "\n" + json.dumps(good_worker(2.0)) + "\n")
        with open(os.path.join(d, "wl-poll.jsonl"), "w") as f:
            f.write(json.dumps(wl_win(100_000, 140_000)) + "\n" + "{not json\n" + json.dumps(wl_win(140_001, 150_000))[:20])
        inp = guardl.collect(d, now=300.0, d1_query=lambda sql: None)
        self.assertEqual(inp["worker"]["ts"], 2.0, "the newest complete reading")
        self.assertEqual(inp["wl_from_ms"], 100_000)
        self.assertEqual([w.get("complete") for w in inp["wl"]], [True, False], "a corrupt line becomes an INCOMPLETE window")
        with open(os.path.join(d, "wl-poll.jsonl"), "a") as f:
            f.write(json.dumps(wl_win(140_001, 150_000))[20:] + "\n")
        inp2 = guardl.collect(d, now=301.0, d1_query=lambda sql: None)
        self.assertEqual([(w["from_ms"], w["to_ms"]) for w in inp2["wl"]], [(140_001, 150_000)], "partial line read once, when complete")
        out = guardl.Guard().tick(300.0, dict(inp, sampler={"ts": 300.0, "subs_ts": 300.0, "subs_ok": True}))
        self.assertTrue(any("not complete" in x for x in out["load"]))
        os.remove(os.path.join(d, "worker-poll.jsonl"))
        self.assertNotIn("worker", guardl.collect(d, now=302.0, d1_query=lambda sql: None))
        shutil.rmtree(d)

    def test_L08_wl_poller_contiguous_and_fail_closed(self):
        calls, d = [], tmpdir()
        res = {"r": {"complete": True, "problems": [], "invocations": [], "records": 0, "pages": 1}}

        def fetch(lo, hi):
            calls.append((lo, hi))
            if res["r"] is None:
                raise TimeoutError("curl")
            return res["r"]
        out = os.path.join(d, "wl-poll.jsonl")
        p = livewin.WLPoller(100_000, fetch, out)
        self.assertIsNone(p.once(150.0), "nothing before the lag has passed")
        w1 = p.once(200.0)
        self.assertEqual((w1["from_ms"], w1["to_ms"], w1["complete"]), (100_000, 140_000, True))
        res["r"] = {"complete": False, "problems": ["records 3 != series total 4"], "invocations": []}
        w2 = p.once(230.0)
        self.assertEqual((w2["from_ms"], w2["complete"]), (140_001, False))
        res["r"] = None
        w3 = p.once(260.0)
        self.assertEqual((w3["from_ms"], w3["complete"]), (140_001, False))
        self.assertTrue(any("query error" in x for x in w3["problems"]))
        res["r"] = {"complete": True, "problems": [], "invocations": []}
        w4 = p.once(290.0)
        self.assertEqual((w4["from_ms"], w4["to_ms"]), (140_001, 230_000), "an incomplete window is re-queried from the same point")
        for w in (w1, w2, w3, w4):
            livewin._append(out, w)
        self.assertEqual(livewin.WLPoller(100_000, fetch, out).next, 230_001, "a restarted poller resumes after the last complete window")
        shutil.rmtree(d)

    def test_L09_end_to_end_obsq_failure_reaches_the_guard(self):
        sys.path.insert(0, os.path.join(REPO, "qa", "b3-stress-harness", "b3obs"))
        import importlib
        obsq = importlib.import_module("obsq")
        t0, recs = 1_800_000_000_000, []
        for i, status in enumerate((200, 500, 502, 200)):
            ts = t0 + i * 1000
            recs.append({"timestamp": ts + 900, "$metadata": {"id": f"i{i}", "service": obsq.SERVICE, "type": "cf-worker-event", "requestId": f"q{i}"},
                         "$workers": {"wallTimeMs": 900, "cpuTimeMs": 5, "outcome": "ok", "scriptName": obsq.SERVICE, "scriptVersion": {"id": "v"},
                                      "eventType": "fetch", "event": {"request": {"url": "https://x.dev/webhooks/orders/paid", "headers": {
                                          "x-shopify-topic": "orders/paid", "x-shopify-webhook-id": f"W{i}", "cf-ray": f"ray{i}"}},
                                          "response": {"status": status}}}})
        d = tmpdir()
        fake = os.path.join(d, "fake.json")
        json.dump({"records": recs}, open(fake, "w"))
        os.environ["OBSQ_FAKE"] = fake
        try:
            obsq._fake_calls[0] = 0
            p = livewin.WLPoller(t0, obsq.fetch_window)
            w = p.once(t0 / 1000 + 2.5 + guardl.WL_LAG_S)
            w2 = p.once(t0 / 1000 + 10 + guardl.WL_LAG_S)
        finally:
            del os.environ["OBSQ_FAKE"]
        self.assertTrue(w["complete"] and w2["complete"])
        g = guardl.Guard()
        out = g.tick(t0 / 1000 + 70, GuardTests.base(None, t0 / 1000 + 70, wl_from_ms=t0, wl=[w]))
        self.assertEqual(out["failwatch"], "LOAD_STOP")
        out = g.tick(t0 / 1000 + 71, GuardTests.base(None, t0 / 1000 + 71, wl=[w2]))
        self.assertEqual(out["failwatch"], "SAFETY_STOP", "2 consecutive failures on orders/paid across two windows")
        self.assertEqual(out["actions"], ["RESTORE_DRY_RUN"])
        shutil.rmtree(d)


def subs_nodes(prefix="https://greenside-entry-allocator-qa.hidden-cherry-619e.workers.dev"):
    topics = ["ORDERS_CANCELLED", "ORDERS_CREATE", "ORDERS_EDITED", "ORDERS_PAID", "REFUNDS_CREATE"]
    return [{"id": f"gid://shopify/WebhookSubscription/{i}", "topic": t,
             "endpoint": {"__typename": "WebhookHttpEndpoint", "callbackUrl": f"{prefix}/webhooks/{t.lower()}"}} for i, t in enumerate(topics)]


def shop_handler(plan, subs=None, draft_status=None, throttle=False, orders=("#1029", "#1028")):
    tags = {r["draft_id"]: r["tag"] for r in plan["rows"]}

    def h(body):
        q = body["query"]
        if "webhookSubscriptions" in q:
            return 200, {}, {"data": {"shop": {"id": "s"}, "webhookSubscriptions": {"nodes": subs if subs is not None else subs_nodes()}}}
        if "L1PreMaxOrder" in q:
            return 200, {}, {"data": {"orders": {"nodes": [{"id": "o", "name": n} for n in orders]}}}
        if "L1DraftsOpen" in q:
            if throttle:
                return 200, {}, {"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}]}
            return 200, {}, {"data": {"nodes": [{"__typename": "DraftOrder", "id": i, "name": "#D", "status": (draft_status or {}).get(i, "OPEN"),
                                                  "tags": ["qa-load", "QAL", tags[i]]} for i in body["variables"]["ids"]]}}
        return 400, {}, {"errors": [{"message": "unexpected document"}]}
    return h


def l1_d1_db(path=":memory:"):
    """QA D1 (allocator migrations) with an unrelated QAE competition and the fresh L1 registration (regsql)."""
    w = World750(n_orders=0)
    if path == ":memory:":
        return w.conn
    disk = sqlite3.connect(path)
    w.conn.backup(disk)
    return disk


def sqlite_query(conn):
    def q(sql):
        cur = conn.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    return q


class StageFourStateFileTests(unittest.TestCase):
    """The state files the live run needs are produced by livewin.py and accepted by their real consumers."""

    def setUp(self):
        self.d = tmpdir()
        self.plan = bound_plan()
        self.sha = plan_hash(self.plan)

    def tearDown(self):
        shutil.rmtree(self.d)

    def client(self, app, docs, handler):
        tx = FakeTx(handler, scope="write_draft_orders" if app == "stress_driver" else "read_orders,read_products")
        return shop.Client(app, docs, env=ENV, transport=tx), tx

    def prep(self, now):
        from sampler import SUBS_DOC
        al, _ = self.client("allocator", {SUBS_DOC, livewin.PRE_MAX_DOC}, shop_handler(self.plan))
        sd, tx = self.client("stress_driver", {livewin.DRAFTS_DOC}, shop_handler(self.plan))
        P = lambda n: os.path.join(self.d, n)
        common.write_json(P("expected-subs.json"), livewin.expected_subs(al, json.load(open(livewin.POLICY))))
        pm = livewin.pre_max_order(al)
        common.write_json(P("pre-max-order.json"), {"pre_max_order_number": pm, "ts": now})
        common.write_json(P("d1-ref.json"), livewin.d1_reference(sqlite_query(l1_d1_db()), L1_CID, pm))
        common.write_json(P("drafts-open.json"), livewin.drafts_open(sd, self.plan, self.sha, now, sleep=lambda s: None))
        for t in ("pre.jsonl", "pre2.jsonl"):
            with open(P(t), "w") as f:
                f.write('{"eventTimestamp": 1}\n')
        return tx

    def arm(self, now):
        P = lambda n: os.path.join(self.d, n)
        wlf = int((now - 100) * 1000)
        for name, text in (("newver.txt", GATE_VER), ("gate.txt", "PASSED"), ("qag-start", str(int(now * 1000))),
                           ("guard.pid", str(os.getpid()))):
            with open(P(name), "w") as f:
                f.write(text + "\n")
        common.write_json(P("guardl-config.json"), livewin.guardl_config(self.plan, self.sha, 1029, wlf, "bash r", "python3 v"))
        common.write_json(P("guardl.json"), {"ts": now, "safety": [], "load": [], "actions": []})
        livewin._append(P("worker-poll.jsonl"), good_worker(now))
        livewin._append(P("wl-poll.jsonl"), wl_win(wlf, int((now - 60) * 1000)))
        livewin._append(P("sampler.jsonl"), {"ts": now, "available": 2000})

    def test_S01_prep_files_and_read_only_documents(self):
        import time as _t
        tx = self.prep(_t.time())
        subs = json.load(open(os.path.join(self.d, "expected-subs.json")))
        self.assertEqual(len(subs), 5)
        self.assertEqual(json.load(open(os.path.join(self.d, "pre-max-order.json")))["pre_max_order_number"], 1029)
        dr = json.load(open(os.path.join(self.d, "drafts-open.json")))
        self.assertEqual((dr["open"], dr["expected"], dr["checked"], dr["plan_sha256"]), (750, 750, 750, self.sha))
        self.assertEqual(len(tx.sent), 15, "750 drafts read back by exact id in batches of 50")
        self.assertTrue(all(b["query"] == livewin.DRAFTS_DOC for b in tx.sent))
        from gs import read_only_graphql
        for doc in (livewin.DRAFTS_DOC, livewin.PRE_MAX_DOC):
            self.assertTrue(read_only_graphql(doc)[0], doc)
        ref = json.load(open(os.path.join(self.d, "d1-ref.json")))
        self.assertEqual(ref["marker"], "SW4C_SNAP")
        self.assertIsInstance(ref["entries"], list, "d1-ref.json is stored parsed, the form allowlist() compares")
        self.assertEqual(guardl.allowlist(ref, ref, L1_CID, [], 1029), [])

    def test_S02_prep_refusals(self):
        from sampler import SUBS_DOC
        pol = json.load(open(livewin.POLICY))
        for subs in (subs_nodes()[:4], subs_nodes(prefix="https://evil.example"), subs_nodes()[:4] + [subs_nodes()[0]]):
            al, _ = self.client("allocator", {SUBS_DOC}, shop_handler(self.plan, subs=subs))
            with self.assertRaises(Refused):
                livewin.expected_subs(al, pol)
        al, _ = self.client("allocator", {livewin.PRE_MAX_DOC}, shop_handler(self.plan, orders=()))
        with self.assertRaises(Refused):
            livewin.pre_max_order(al)
        rid = self.plan["rows"][7]["draft_id"]
        sd, _ = self.client("stress_driver", {livewin.DRAFTS_DOC}, shop_handler(self.plan, draft_status={rid: "COMPLETED"}))
        dr = livewin.drafts_open(sd, self.plan, self.sha, 1.0, sleep=lambda s: None)
        self.assertEqual((dr["open"], dr["problem_count"]), (749, 1))
        sd, _ = self.client("stress_driver", {livewin.DRAFTS_DOC}, shop_handler(self.plan, throttle=True))
        with self.assertRaises(Refused):
            livewin.drafts_open(sd, self.plan, self.sha, 1.0, sleep=lambda s: None)
        conn = l1_d1_db()
        w = World750.__new__(World750); w.conn = conn
        w._alloc(L1_CID, oid(1), "#1030", lid(1), 1, "QAL", [1001], "2026-10-06T11:00:00.000Z")
        with self.assertRaises(Refused):
            livewin.d1_reference(sqlite_query(conn), L1_CID, 1029)
        with self.assertRaises(Refused):
            livewin.d1_reference(lambda sql: None, L1_CID, 1029)
        with self.assertRaises(Refused):
            livewin.d1_reference(sqlite_query(l1_d1_db()), "424242", 1029)

    def test_S03_check_state_phases(self):
        import time as _t
        now = _t.time()
        self.prep(now)
        self.assertEqual(livewin.check_state(self.d, self.plan, self.sha, "prelive", now)[0], [])
        self.assertTrue(livewin.check_state(self.d, self.plan, self.sha, "armed", now)[0], "armed needs the gate and guard files")
        self.arm(now)
        probs, files = livewin.check_state(self.d, self.plan, self.sha, "armed", now)
        self.assertEqual(probs, [])
        for f in ("guardl-config.json", "d1-ref.json", "drafts-open.json", "pre.jsonl", "pre2.jsonl", "newver.txt", "gate.txt", "qag-start"):
            self.assertIn(f, files)
        P = lambda n: os.path.join(self.d, n)
        bad = [("qag-start", str(int((now - 1300) * 1000))), ("gate.txt", "GATE_FAILED"), ("newver.txt", "")]
        for name, text in bad:
            keep = open(P(name)).read()
            with open(P(name), "w") as f:
                f.write(text + "\n")
            self.assertTrue(livewin.check_state(self.d, self.plan, self.sha, "armed", now)[0], name)
            with open(P(name), "w") as f:
                f.write(keep)
        for name in ("expected-subs.json", "d1-ref.json", "drafts-open.json", "guardl-config.json", "pre.jsonl", "guardl.json",
                     "worker-poll.jsonl", "wl-poll.jsonl", "sampler.jsonl", "guard.pid"):
            os.rename(P(name), P(name + ".x"))
            self.assertTrue(livewin.check_state(self.d, self.plan, self.sha, "armed", now)[0], f"{name} missing must fail")
            os.rename(P(name + ".x"), P(name))
        self.assertTrue(livewin.check_state(self.d, self.plan, self.sha, "armed", now + 1900)[0], "drafts-open older than 30 min")
        dr = json.load(open(P("drafts-open.json")))
        common.write_json(P("drafts-open.json"), dict(dr, ts=now - 1900))
        self.assertEqual(livewin.check_state(self.d, self.plan, self.sha, "prelive", now)[0], ["drafts-open.json older than 30 min"])
        common.write_json(P("drafts-open.json"), dr)
        common.write_json(P("guardl.json"), {"ts": now, "safety": [], "load": ["Workers Logs missing or stale"], "actions": []})
        self.assertTrue(livewin.check_state(self.d, self.plan, self.sha, "armed", now)[0])
        common.write_json(P("guardl.json"), {"ts": now, "safety": [], "load": [], "actions": []})
        livewin._append(P("worker-poll.jsonl"), good_worker(now, dry_run="true"))
        self.assertTrue(livewin.check_state(self.d, self.plan, self.sha, "armed", now)[0])
        livewin._append(P("worker-poll.jsonl"), good_worker(now))
        open(P(f"load-{L1_CID}.done"), "w").close()
        self.assertTrue(livewin.check_state(self.d, self.plan, self.sha, "armed", now)[0], "one-shot marker")

    def test_S04_files_accepted_by_their_real_consumers(self):
        import time as _t
        now = _t.time()
        self.prep(now)
        self.arm(now)
        # fire.py check_live_gates (what load.py live runs): the gate/arming/guard files plus a Worker live on newver.txt
        self.assertEqual(FIRE.check_live_gates(FakeCF(dry=("false",)), CF_ENV, self.d, now), [])
        self.assertTrue(FIRE.check_live_gates(FakeCF(dry=("true",)), CF_ENV, self.d, now))
        # load.py live's drafts-open precheck (same expression)
        pre = json.load(open(os.path.join(self.d, "drafts-open.json")))
        self.assertTrue(pre.get("plan_sha256") == self.sha and now - pre.get("ts", 0) <= load.PRECHECK_MAX_AGE_S and pre.get("open") == len(self.plan["rows"]))
        # sampler.py's expected subscriptions compare equal to a live snapshot of the same subscriptions
        from sampler import subs_match, subs_of
        self.assertTrue(subs_match(subs_of({"data": {"webhookSubscriptions": {"nodes": subs_nodes()}}}),
                                   json.load(open(os.path.join(self.d, "expected-subs.json")))))
        # guardl collect + tick on these files: clear, with the D1 count and allow-list read from the reference database
        conn = l1_d1_db()
        inp = guardl.collect(self.d, now=now, d1_query=sqlite_query(conn))
        self.assertEqual(inp["allowlist"]["deviations"], [])
        g = guardl.Guard()
        out = g.tick(now, inp)
        self.assertEqual((out["safety"], out["load"], out["actions"]), ([], [], []))
        # the guard config carries the restore/verify commands guardl.sh runs and the Workers Logs start
        cfg = json.load(open(os.path.join(self.d, "guardl-config.json")))
        self.assertEqual(set(cfg), {"competition_id", "pre_max_order_number", "repo", "restore_cmd", "verify_cmd", "wl_from_ms", "plan_sha256"})
        with self.assertRaises(Refused):
            livewin.guardl_config(self.plan, self.sha, 1029, None, "r", "v")

    def test_S05_final_failwatch_and_coverage(self):
        P = lambda n: os.path.join(self.d, n)
        common.write_json(P("guardl-config.json"), {"wl_from_ms": 100_000})
        o = {"eventTimestamp": 120000, "wallTime": 900, "outcome": "ok", "event": {"request": {"url": "https://x.dev/webhooks/orders/paid",
             "headers": {"x-shopify-topic": "orders/paid", "x-shopify-webhook-id": "W1", "cf-ray": "R1"}}, "response": {"status": 500}}, "logs": []}
        with open(P("pre.jsonl"), "w") as f:
            f.write(json.dumps(o) + "\n")
        for w in (wl_win(100_000, 140_000), wl_win(140_001, 150_000, complete=False), wl_win(160_000, 170_000)):
            livewin._append(P("wl-poll.jsonl"), w)
        out = livewin.final(self.d)
        self.assertEqual(out["failwatch"]["level"], "LOAD_STOP")
        self.assertEqual(out["wl_coverage"]["gaps"], [[140_001, 159_999]])
        self.assertEqual(out["wl_coverage"]["incomplete_windows"], 1)
        self.assertTrue(os.path.exists(P("failwatch-final.json")))

    def test_S06_governor_thresholds_and_b3_mechanisms_unchanged(self):
        """The wiring changes no governor, threshold, failwatch rule, driver or B3 live-control script (sha256 at ef98661)."""
        pinned = {
            "qa/l1-load-harness/l1/governor.py": "eb86ffb442089c1dab49ba6ae9be303d49e9dfd862794d808eee896c4663f7f9",
            "qa/l1-load-harness/l1/common.py": "9248e02bb11f7ef4d5ddac11557982c5ab361e0f63a50238816df5e6e8741853",
            "qa/l1-load-harness/l1/failwatch.py": "23f9b2c115b78beb78208ca42655edc3f4039d2bd2d58b28709600b4ad5c26bd",
            "qa/l1-load-harness/l1/load.py": "d7e81f31452cb08006be6384963d2fef76d8a333866756caeee201897fd4998e",
            "qa/l1-load-harness/l1/sampler.py": "9a41c6c126835a4027d0e92da8d92e9065da762b6bfe63f63e7b0bd44aa3f55a",
            "qa/b3-stress-harness/b3stress/gate.sh": "93a1d7f3603ab734da25b5c620c655aefd23f59f1f3ac24406982f780e0998e1",
            "qa/b3-stress-harness/b3stress/restore.sh": "73926f0fb951d8ca232816188502be8e6afc44ae81074d4b562eafc9badc54e5",
            "qa/b3-stress-harness/b3stress/strictgate.sh": "6d0a3ecbf7c260ac64faa8542d84987b2f121bbbf71d54a4cfa2084fce0c6f76",
            "qa/b3-stress-harness/b3stress/fire.py": "a68efef2cd0e7fed87402d6df917461205db987a68fee42a91ba5469942c4ca7",
            "qa/b3-stress-harness/b3obs/obsq.py": "98a56e2ec29b6a4ba9995c310c0dc86f6e3744d8b5026ca88a15fba87c82525b",
        }
        for rel, sha in pinned.items():
            self.assertEqual(common.sha256_file(os.path.join(REPO, rel)), sha, rel)


# ---- window.sh, run end to end in a sandbox: real window.sh and real livewin.py, stubbed network and stubbed B3 scripts ----------
STUB_LIVEWIN = r"""
import json, os, sqlite3, sys, types
sys.path.insert(0, os.environ["L1_REAL"])
import livewin, shop
F, D = os.environ["L1_FAKE"], os.environ["L1_SANDBOX_D"]
with open(os.path.join(F, "calls.log"), "a") as fh:
    fh.write("livewin " + " ".join(a for a in sys.argv[1:] if not a.startswith("/")) + "\n")

def rd(p):
    try:
        return open(p).read().strip()
    except OSError:
        return None

class FakeFire:
    PROD_WORKER, QA_WORKER = "greenside-entry-allocator", "greenside-entry-allocator-qa"
    class CloudflareTransport:
        pass
    def cf_json(self, cf, path, tok):
        dry = rd(os.path.join(F, "dry")) or "true"
        if path.endswith("/greenside-entry-allocator/settings"):
            return 404, {"success": False}
        if path.endswith("/settings"):
            return 200, {"success": True, "result": {"bindings": [{"name": "DRY_RUN", "text": dry}]}}
        ver = rd(os.path.join(D, "restorever.txt")) if os.path.exists(os.path.join(F, "restored")) else (rd(os.path.join(D, "newver.txt")) if dry == "false" else "dddddddd-0000-0000-0000-000000000000")
        return 200, {"success": True, "result": {"deployments": [{"versions": [{"version_id": ver, "percentage": 100}]}]}}

class FakeClient:
    def __init__(self, app, docs, env=None, transport=None):
        self.app, self.secrets = app, []
    def scopes(self):
        return {"write_draft_orders"} if self.app == "stress_driver" else {"read_orders", "read_products"}
    def post(self, doc, variables=None):
        plan = json.load(open(os.environ["L1_PLAN"]))
        tags = {r["draft_id"]: r["tag"] for r in plan["rows"]}
        if "webhookSubscriptions" in doc:
            p = "https://greenside-entry-allocator-qa.hidden-cherry-619e.workers.dev/webhooks/"
            return 200, {}, {"data": {"webhookSubscriptions": {"nodes": [{"id": str(i), "topic": t, "endpoint": {"callbackUrl": p + t}} for i, t in enumerate(
                ["ORDERS_CANCELLED", "ORDERS_CREATE", "ORDERS_EDITED", "ORDERS_PAID", "REFUNDS_CREATE"])]}}}
        if "L1PreMaxOrder" in doc:
            return 200, {}, {"data": {"orders": {"nodes": [{"id": "o", "name": "#1029"}]}}}
        return 200, {}, {"data": {"nodes": [{"__typename": "DraftOrder", "id": i, "status": "OPEN", "tags": ["qa-load", "QAL", tags[i]]}
                                            for i in variables["ids"]]}}

def d1(sql):
    conn = sqlite3.connect(os.path.join(F, "qa.db"))
    cur = conn.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]

livewin._fire = lambda: FakeFire()
shop.Client = FakeClient
livewin._gs_d1 = d1
livewin.in_slot = lambda now: True
livewin.install_problems = lambda s, run=None: []
livewin.prelive_sweeps = lambda d, since, ver, run=None, clock=None: (True, {"stub": "B3 prestrict + evidence"})
livewin.webhooks_since = lambda d, since, run=None: 0
livewin._obsq = lambda: types.SimpleNamespace(fetch_window=lambda lo, hi: {"complete": True, "problems": [], "invocations": [], "records": 0, "pages": 1})
livewin.WL_LAG_S = 1; livewin.WL_POLL_S = 1; livewin.WORKER_POLL_S = 1
livewin.DRAFTS_BATCH = 250
sys.exit(livewin.main(sys.argv[1:]))
"""

STUBS_B3 = {
    "gate.sh": 'echo gate.sh >> "$F/calls.log"; date -u +%s%3N > "$D/t0.txt"; touch "$D/conf-wanted"\n'
               'if [ -f "$F/gate-fail" ]; then echo GATE_FAILED > "$D/gate.txt"; bash "$D/restore.sh"; exit 0; fi\n'
               'echo 11111111-2222-3333-4444-555555555555 > "$D/newver.txt"; echo false > "$F/dry"; echo PASSED > "$D/gate.txt"\n',
    "restore.sh": 'echo restore.sh >> "$F/calls.log"; sleep 1; echo 99999999-8888-7777-6666-555555555555 > "$D/restorever.txt"\n'
                  'echo true > "$F/dry"; touch "$F/restored"; echo PASSED > "$D/restoregate.txt"\n',
    "postcheck.sh": 'echo postcheck.sh >> "$F/calls.log"; echo PASSED > "$D/postcheck.txt"; touch "$D/stop"\n',
    "dsnap.sh": 'echo "dsnap.sh $(basename $1)" >> "$F/calls.log"; echo "{}" > "$1"\n',
    "supervisor.sh": 'while [ ! -f "$D/stop" ]; do echo \'{"eventTimestamp": 1}\' >> "$D/pre.jsonl"; echo \'{"eventTimestamp": 1}\' >> "$D/pre2.jsonl"; sleep 1; done\n',
    "heartbeat.sh": 'while [ ! -f "$D/stop" ]; do sleep 1; done\n',
}

STUBS_L1 = {
    "guardl.sh": 'D="$1"; echo $$ > "$D/guard.pid"; echo guardl.sh >> "$F/calls.log"; [ -f "$F/guard-die" ] && exit 1\n'
                 'R=$(python3 -c "import json;print(json.load(open(\'$D/guardl-config.json\'))[\'restore_cmd\'])")\n'
                 'V=$(python3 -c "import json;print(json.load(open(\'$D/guardl-config.json\'))[\'verify_cmd\'])")\n'
                 'while :; do python3 -c "import json,time;json.dump({\'ts\':time.time(),\'safety\':[],\'load\':[],\'actions\':[]},open(\'$D/guardl.json\',\'w\'))"\n'
                 '  if [ -f "$D/load-summary.json" ] || [ -f "$D/manual-stop" ]; then echo "guard restore" >> "$F/calls.log"; bash -c "$R"\n'
                 '    [ "$(bash -c "$V")" = true ] && { echo "guard verified" >> "$F/calls.log"; exit 0; }; fi\n'
                 '  sleep 1; done\n',
    "sampler.py": 'import json, os, sys, time\nF, D = os.environ["L1_FAKE"], os.environ["L1_SANDBOX_D"]\n'
                  'open(os.path.join(F, "calls.log"), "a").write("sampler.py\\n")\n'
                  'while not os.path.exists(os.path.join(D, "stop")):\n'
                  '    open(os.path.join(D, "sampler.jsonl"), "a").write(json.dumps({"ts": time.time(), "available": 2000}) + "\\n"); time.sleep(1)\n',
    "load.py": 'import json, os, sys, time\nF, D = os.environ["L1_FAKE"], os.environ["L1_SANDBOX_D"]\n'
               'ok = os.path.exists(os.path.join(D, "qag-start")) and os.path.exists(os.path.join(D, "guard.pid"))\n'
               'open(os.path.join(F, "calls.log"), "a").write(f"load.py {sys.argv[1]} armed={ok} confirm={sys.argv[sys.argv.index(\'--confirm\') + 1]}\\n")\n'
               'if os.path.exists(os.path.join(F, "load-refuse")): sys.exit(2)\n'
               'json.dump({"dispatched": 750}, open(os.path.join(D, "load-summary.json"), "w")); sys.exit(0)\n',
}


class StageFourRunbookTests(unittest.TestCase):
    """window.sh sequencing with the real window.sh and livewin.py; network and B3 live scripts stubbed; no network possible
    (the proxy points at a closed port)."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="l1win-")
        self.S, self.F = os.path.join(self.root, "S"), os.path.join(self.root, "fake")
        self.D, self.H = os.path.join(self.S, "b3stress"), os.path.join(self.root, "l1")
        for x in (self.D, self.F, self.H):
            os.makedirs(x)
        for name, body in STUBS_B3.items():
            with open(os.path.join(self.D, name), "w") as f:
                f.write(f'#!/usr/bin/env bash\nD="{self.D}"; F="{self.F}"\n' + body)
            os.chmod(os.path.join(self.D, name), 0o755)
        shutil.copy(os.path.join(L1, "window.sh"), os.path.join(self.H, "window.sh"))
        with open(os.path.join(self.H, "livewin.py"), "w") as f:
            f.write(STUB_LIVEWIN)
        for name, body in STUBS_L1.items():
            with open(os.path.join(self.H, name), "w") as f:
                f.write((f'#!/usr/bin/env bash\nF="{self.F}"\n' if name.endswith(".sh") else "") + body)
        self.plan = bound_plan()
        self.sha = plan_hash(self.plan)
        self.planf = os.path.join(self.root, "plan-bound.json")
        common.write_json(self.planf, self.plan)
        l1_d1_db(os.path.join(self.F, "qa.db")).close()
        with open(os.path.join(self.F, "dry"), "w") as f:
            f.write("true\n")
        self.env = {k: v for k, v in os.environ.items() if not any(t in k for t in ("TOKEN", "SECRET", "CLIENT_ID", "ACCOUNT"))}
        self.env.update(CF_ENV, HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9",
                        L1_REAL=L1, L1_FAKE=self.F, L1_SANDBOX_D=self.D, L1_PLAN=self.planf, PYTHONDONTWRITEBYTECODE="1")
        # monitors already running (as after an earlier prelive), so window.sh does not wait 12 s for them
        import time as _t
        with open(os.path.join(self.D, "mon-start-ms.txt"), "w") as f:
            f.write(str(int(_t.time() * 1000)) + "\n")
        self.sup = subprocess.Popen(["bash", os.path.join(self.D, "supervisor.sh")], start_new_session=True)

    def tearDown(self):
        import time as _t
        open(os.path.join(self.D, "stop"), "a").close()
        _t.sleep(2.5)
        for pidf in ("poll-wl.pid", "poll-worker.pid", "sampler.pid", "guard.pid"):
            try:
                os.kill(int(open(os.path.join(self.D, pidf)).read()), 9)
            except (OSError, ValueError):
                pass
        self.sup.kill()
        shutil.rmtree(self.root, ignore_errors=True)

    def window(self, *args, timeout=180):
        return subprocess.run(["bash", os.path.join(self.H, "window.sh"), *args], env=self.env, capture_output=True, text=True,
                              timeout=timeout)

    def calls(self):
        try:
            return open(os.path.join(self.F, "calls.log")).read().splitlines()
        except OSError:
            return []

    def first(self, calls, prefix):
        return next(i for i, c in enumerate(calls) if c.startswith(prefix))

    def test_R01_live_refused_without_phrase(self):
        for extra in ([], ["LOAD-QAL-750-ORDERS"], ["load-qal-750-orders-1650-entries"]):
            r = self.window("live", self.S, self.planf, self.sha, *extra)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(self.calls(), [], "nothing at all runs without the phrase")

    def test_R02_prelive_deploys_nothing(self):
        r = self.window("prelive", self.S, self.planf, self.sha)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        c = self.calls()
        self.assertFalse([x for x in c if x.startswith(("gate.sh", "restore.sh", "load.py", "guardl.sh", "livewin config"))], c)
        order = ["livewin install-check", "livewin worker-before", "livewin prep", "livewin slot", "livewin prelive-sweeps",
                 "livewin no-webhooks", "livewin d1-same", "livewin drafts-open", "livewin check-state"]
        self.assertEqual([self.first(c, o) for o in order], sorted(self.first(c, o) for o in order), c)
        st = json.load(open(os.path.join(self.D, "window-state-prelive.json")))
        self.assertTrue(st["ok"], st)
        for f in ("expected-subs.json", "pre-max-order.json", "d1-ref.json", "drafts-open.json", "worker-before.json", "dry-version.txt"):
            self.assertTrue(os.path.exists(os.path.join(self.D, f)), f)

    def test_R03_live_sequence_and_state_files(self):
        r = self.window("live", self.S, self.planf, self.sha, CONFIRM_LIVE)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
        c = self.calls()
        order = ["livewin check-state", "gate.sh", "livewin config", "load.py live armed=True", "guard restore", "restore.sh",
                 "guard verified", "dsnap.sh d1-final.json", "postcheck.sh", "livewin final"]
        idx = [self.first(c, o) for o in order]
        self.assertEqual(idx, sorted(idx), c)
        for bg in ("livewin poll-worker", "sampler.py", "guardl.sh"):        # started after the config, all running before GO
            self.assertTrue(self.first(c, "livewin config") < self.first(c, bg) < self.first(c, "load.py"), bg)
        self.assertIn("livewin poll-wl", " ".join(c))     # started before gate.sh; its order is proven by wl-from-ms <= t0 below
        self.assertEqual(sum(1 for x in c if x == "restore.sh"), 1, "exactly one restore deploy")
        self.assertEqual(sum(1 for x in c if x == "gate.sh"), 1)
        self.assertIn(f"confirm={CONFIRM_LIVE}", next(x for x in c if x.startswith("load.py")))
        P = lambda n: os.path.join(self.D, n)
        for f in ("guardl-config.json", "d1-ref.json", "drafts-open.json", "pre.jsonl", "pre2.jsonl", "newver.txt", "gate.txt", "qag-start",
                  "expected-subs.json", "worker-poll.jsonl", "wl-poll.jsonl", "failwatch-final.json", "d1-final.json"):
            self.assertTrue(os.path.exists(P(f)), f)
        self.assertTrue(json.load(open(P("window-state-armed.json")))["ok"])
        self.assertTrue(json.load(open(P("window-state-guarded.json")))["ok"])
        cfg = json.load(open(P("guardl-config.json")))
        self.assertEqual(cfg["competition_id"], L1_CID)
        self.assertEqual(cfg["wl_from_ms"], int(open(P("wl-from-ms.txt")).read()))
        self.assertLessEqual(cfg["wl_from_ms"], int(open(P("t0.txt")).read()), "Workers Logs coverage starts before the deploy")
        self.assertEqual(cfg["restore_cmd"], f"bash {self.H}/window.sh restore {self.S}")
        self.assertTrue(cfg["verify_cmd"].endswith(f"livewin.py verify-restore --state-dir {self.D}"))
        w = guardl.last_jsonl(P("worker-poll.jsonl"))
        self.assertEqual(w["dry_run"], "true", "the poller saw the restore")
        lines = [json.loads(x) for x in open(P("worker-poll.jsonl")).read().splitlines()]
        self.assertTrue(any(x["dry_run"] == "false" and x["versions"] == [[open(P("newver.txt")).read().strip(), 100]] for x in lines))
        self.assertGreaterEqual(int(open(P("qag-start")).read()), int(open(P("t0.txt")).read()), "armed only after the gate")

    def test_R04_gate_failure_arms_nothing(self):
        open(os.path.join(self.F, "gate-fail"), "w").close()
        r = self.window("live", self.S, self.planf, self.sha, CONFIRM_LIVE)
        self.assertEqual(r.returncode, 2, r.stdout[-2000:])
        c = self.calls()
        self.assertGreater(self.first(c, "restore.sh"), self.first(c, "gate.sh"), "gate.sh restores by itself, as before")
        self.assertEqual(c.count("restore.sh"), 1)
        self.assertFalse([x for x in c if x.startswith(("livewin config", "guardl.sh", "load.py", "livewin poll-worker"))], c)
        self.assertFalse(os.path.exists(os.path.join(self.D, "qag-start")))
        import time as _t
        _t.sleep(1)
        with self.assertRaises(OSError, msg="a failed gate stops the Workers Logs poller"):
            os.kill(int(open(os.path.join(self.D, "poll-wl.pid")).read()), 0)

    def test_R05_failure_before_gate_deploys_nothing(self):
        with open(os.path.join(self.F, "dry"), "w") as f:
            f.write("false\n")                 # the QA Worker is already live: worker-before refuses
        r = self.window("live", self.S, self.planf, self.sha, CONFIRM_LIVE)
        self.assertEqual(r.returncode, 2)
        self.assertFalse([x for x in self.calls() if x.startswith(("gate.sh", "restore.sh", "load.py"))], self.calls())

    def test_R06_restore_wrapper_serialised_and_idempotent(self):
        P = lambda n: os.path.join(self.D, n)
        r = self.window("restore", self.S)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.calls().count("restore.sh"), 1)
        r = self.window("restore", self.S)
        self.assertIn("already verified", r.stdout)
        self.assertEqual(self.calls().count("restore.sh"), 1, "a verified restore is not deployed again")
        for f in ("restorever.txt", "restoregate.txt"):
            os.remove(P(f))
        os.remove(os.path.join(self.F, "restored"))
        with open(os.path.join(self.F, "dry"), "w") as f:
            f.write("false\n")
        ps = [subprocess.Popen(["bash", os.path.join(self.H, "window.sh"), "restore", self.S], env=self.env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True) for _ in range(2)]
        outs = [p.communicate(timeout=60)[0] for p in ps]
        self.assertEqual(self.calls().count("restore.sh"), 2, f"two concurrent callers deploy once between them: {outs}")
        self.assertEqual(sum("already verified" in o for o in outs), 1)

    def test_R08_failure_after_gate_without_a_guard_restores_directly(self):
        open(os.path.join(self.F, "guard-die"), "w").close()
        r = self.window("live", self.S, self.planf, self.sha, CONFIRM_LIVE)
        self.assertEqual(r.returncode, 3, r.stdout[-2000:])
        c = self.calls()
        self.assertFalse([x for x in c if x.startswith("load.py")], "nothing is sent")
        self.assertFalse(os.path.exists(os.path.join(self.D, "qag-start")), "nothing is armed")
        idx = [self.first(c, o) for o in ("gate.sh", "guardl.sh", "restore.sh", "dsnap.sh d1-final.json", "postcheck.sh", "livewin final")]
        self.assertEqual(idx, sorted(idx), c)
        self.assertEqual(c.count("restore.sh"), 1)
        self.assertIn("restore VERIFIED", open(os.path.join(self.D, "window.log")).read())

    def test_R07_driver_refusal_restores_through_the_guard(self):
        open(os.path.join(self.F, "load-refuse"), "w").close()
        r = self.window("live", self.S, self.planf, self.sha, CONFIRM_LIVE)
        self.assertEqual(r.returncode, 3, r.stdout[-2000:])
        c = self.calls()
        self.assertTrue(os.path.exists(os.path.join(self.D, "manual-stop")))
        idx = [self.first(c, o) for o in ("load.py", "guard restore", "restore.sh", "guard verified", "postcheck.sh")]
        self.assertEqual(idx, sorted(idx), c)



class StageFourInstallTests(unittest.TestCase):
    """__SCRATCHPAD__ is resolved by the EXISTING B3 install.sh, run by window.sh install; a used L1 state directory is refused."""

    def setUp(self):
        self.S = tempfile.mkdtemp(prefix="l1inst-")
        self.env = {k: v for k, v in os.environ.items() if not any(t in k for t in ("TOKEN", "SECRET", "CLIENT_ID", "ACCOUNT"))}
        self.env.update(HTTPS_PROXY="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9", PYTHONDONTWRITEBYTECODE="1")

    def tearDown(self):
        shutil.rmtree(self.S, ignore_errors=True)

    def install(self):
        return subprocess.run(["bash", os.path.join(L1, "window.sh"), "install", self.S], env=self.env, capture_output=True, text=True,
                              timeout=120)

    def test_I01_install_resolves_the_placeholder(self):
        r = self.install()
        self.assertEqual(r.returncode, 2, "the allocator worktree is still to be added, so the install check reports it")
        self.assertIn("installed into", r.stdout)
        D = os.path.join(self.S, "b3stress")
        for f in ("gate.sh", "restore.sh", "strictgate.sh", "postcheck.sh", "dsnap.sh", "evidence.py"):
            t = open(os.path.join(D, f)).read()
            self.assertNotIn("__SCRATCHPAD__", t, f)
            self.assertIn(self.S, t, f)
        probs = json.loads(r.stdout.strip().splitlines()[-2])["problems"]
        self.assertTrue(probs and all("allocator worktree" in p for p in probs), probs)
        os.makedirs(os.path.join(self.S, "b3qa", "greenside-entry-allocator", "node_modules"))
        fake_git = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, livewin.ALLOCATOR_COMMIT + "\n" if "rev-parse" in cmd else "", "")
        self.assertEqual(livewin.install_problems(self.S, run=fake_git), [])
        dirty = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, livewin.ALLOCATOR_COMMIT + "\n" if "rev-parse" in cmd else " M src/x.ts\n", "")
        self.assertTrue(livewin.install_problems(self.S, run=dirty), "a dirty worktree is refused")
        with open(os.path.join(D, "gate.sh"), "a") as f:
            f.write("# __SCRATCHPAD__\n")
        self.assertTrue(any("placeholder" in p for p in livewin.install_problems(self.S, run=fake_git)))

    def test_I02_used_state_directory_is_never_reinstalled(self):
        self.install()
        D = os.path.join(self.S, "b3stress")
        for used in (f"load-{L1_CID}.done", "qag-start", "guardl-config.json"):
            open(os.path.join(D, used), "w").close()
            r = self.install()
            self.assertEqual(r.returncode, 2)
            self.assertIn("REFUSED", r.stdout)
            self.assertTrue(os.path.exists(os.path.join(D, used)), "install.sh never ran over the used directory")
            self.assertTrue(any("L1 run state" in p for p in livewin.install_problems(self.S)), used)
            os.remove(os.path.join(D, used))


if __name__ == "__main__":
    unittest.main(verbosity=2)
