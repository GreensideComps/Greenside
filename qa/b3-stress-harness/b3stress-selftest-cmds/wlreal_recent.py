#!/usr/bin/env python3
"""OPT-IN, READ-ONLY real Workers Logs check against a RECENT rolling window (never part of run-all.sh).
Replaces the old fixed-date real checks (A13/A14/C1-C5 in wltest.py, which query 28 Sep 2026 windows that have aged out of the 7-day
Workers Logs retention; they still run with `wltest.py ... ` without --no-real while their data exists). Paging, completeness rules and
invalid-record handling are covered offline by wltest.py A1-A12; this checks that the same obsq.fetch_window rules hold on live data.
Usage: wlreal_recent.py B3OBS_DIR [MINUTES]   (needs CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID; queries the QA Worker only)
Window: [now - MINUTES - 2 min, now - 2 min] (default 35 min: always contains at least two */15 cron sweeps; 2 min > ingestion delay)."""
import sys
sys.dont_write_bytecode = True
import time
sys.path.insert(0, sys.argv[1])
import obsq

mins = int(sys.argv[2]) if len(sys.argv) > 2 else 35
t1 = int((time.time() - 120) * 1000); t0 = t1 - mins * 60 * 1000
r = obsq.fetch_window(t0, t1)
res = []
def chk(name, ok, detail=""):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)[:300]))
inv = r["invocations"]
crons = [x for x in inv if (x.get("event") or {}).get("cron")]
chk(f"R1 real rolling window ({mins} min, ending 2 min ago) is COMPLETE under the obsq rules (no problems)", r["complete"] and not r["problems"], r["problems"])
chk("R2 records == series total and every sampleInterval == 1", r["records"] == r["series_total"] and set(r["sample_intervals"]) <= {1}, {k: r[k] for k in ("records", "series_total", "sample_intervals")})
chk("R3 no invalid invocation groups", not r["invalid"], r["invalid"][:3])
chk(f"R4 at least {mins // 15 - 1} cron invocation(s) present (QA crons */15 and 20,50)", len(crons) >= max(1, mins // 15 - 1), len(crons))
chk("R5 every invocation has a start timestamp and a script version", all(x.get("eventTimestamp") and (x.get("scriptVersion") or {}).get("id") for x in inv), len(inv))
print(f"pages {r['pages']} records {r['records']} invocations {len(inv)} crons {len(crons)}")
print(f"WORKERS LOGS REAL (rolling): {sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
