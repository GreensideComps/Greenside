#!/usr/bin/env python3
"""L1 offline tests (Stage 1). No network, no Shopify, no D1, no Worker: every external is a fake or a local SQLite database built
from the allocator's own migrations (git show e917bb5). Test names carry the requirement number (R01..R38) they prove.
Run: python3 -m unittest -v test_l1   (from this directory)  or  ../run-tests.sh"""
import contextlib, hashlib, io, json, os, random, shutil, sqlite3, subprocess, sys, tempfile, unittest

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
        r = sim_run(28.0)
        seq = ["A"] + [x["event"].split("->")[1] for x in r["gov"].log if x["event"].startswith("phase")]
        self.assertEqual(seq, ["A", "B", "C", "E"])
        # the B order cap cut the 3.5/s step short: it is INCOMPLETE, never promoted; the bracket stays open
        g = r["gov"].summary()
        self.assertEqual(g["steps"][-1]["result"], "INCOMPLETE")
        self.assertEqual(g["r_star"], 3.0)
        self.assertEqual(g["stability_bracket"], {"low": 3.0, "high": None, "bracketed": False, "why": "B order cap reached"})

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
        # pass 1.0 and 1.5
        for k in range(2):
            now += 41
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
        g = Governor(approve_t=APPROVE_T)
        self.assertTrue(g.t_on and not g.t2_on)
        r = sim_run(28.0, gov=Governor(approve_t=APPROVE_T))
        self.assertEqual(r["gov"].sent["T"], 40)
        self.assertEqual(r["gov"].sent["T2"], 0)
        r = sim_run(28.0, gov=Governor(approve_t=APPROVE_T, approve_t2=APPROVE_T2))
        self.assertEqual((r["gov"].sent["T"], r["gov"].sent["T2"]), (20, 20))


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
            if "draftOrders(first" in body["query"]:
                return 200, {}, {"data": {"draftOrders": {"nodes": state["existing"]}}}
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
            if "draftOrders(first" in body["query"]:
                return 200, {}, {"data": {"draftOrders": {"nodes": []}}}
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
class GuardTests(unittest.TestCase):
    def base(self, now, **kw):
        d = {"sampler": {"ts": now, "subs_ts": now, "subs_ok": True}, "d1_count": {"ts": now, "allocated": kw.pop("allocated", 0)},
             "worker": {"dry_run": "false"}, "driver": {"started": 0.0, "done": False, "last_sent_ts": None, "completed_units": 0}}
        d.update(kw)
        return d

    def test_safety_sources(self):
        cases = [dict(sampler={"ts": 1, "subs_ts": 1, "subs_ok": False}), dict(manual_stop=True), dict(load_safety_file=True),
                 dict(worker={"dry_run": "false", "production_present": True}), dict(worker={"dry_run": "true"}),
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
