#!/usr/bin/env python3
"""H6 regsql.py: the L1 QA D1 registration SQL, chunked under D1's 100 KB statement limit (pure; no network).

Same statements as the allocator's own registration (src/db.ts UPSERT_COMPETITION as DRAFT, INSERT_POOL_ROW x capacity,
MARK_POOL_BUILT) and as b3stress/register.sh, with literals. The pool is written as multi-row INSERTs, each file at most
MAX_BYTES (90,000, i.e. 10% under the limit). Output files are applied IN ORDER by register.sh:
  00-competition.sql   INSERT competition ... 'DRAFT'
  01..NN-pool.sql      INSERT INTO entry_number ... VALUES (...),(...)   (AVAILABLE, allocation_seq 0)
  99-open.sql          UPDATE competition SET status='OPEN' ... WHERE status='DRAFT'
  regsql.py write --competition ID --out DIR [--at ISO]"""
import argparse, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import CAPACITY, PAD_WIDTH, PREFIX, PRODUCT_HANDLE, PRODUCT_TITLE, RETIRED_COMPETITIONS, START_NUMBER, Refused, entry_number  # noqa: E402

D1_STATEMENT_LIMIT = 100_000
MAX_BYTES = 90_000
SHOP = "7r5csb-1j.myshopify.com"


def q(s):
    return "'" + str(s).replace("'", "''") + "'"


def competition_sql(cid, at):
    return ("INSERT INTO competition (competition_id, shop_domain, product_gid, prefix, start_number, capacity, pad_width, "
            "handle_snapshot, title_snapshot, skill_question_snapshot, skill_answers_snapshot, skill_answer_correct_snapshot, "
            f"status, created_at, updated_at) VALUES ({q(cid)}, {q(SHOP)}, {q('gid://shopify/Product/' + cid)}, {q(PREFIX)}, "
            f"{START_NUMBER}, {CAPACITY}, {PAD_WIDTH}, {q(PRODUCT_HANDLE)}, {q(PRODUCT_TITLE)}, NULL, NULL, NULL, 'DRAFT', {q(at)}, {q(at)});")


def open_sql(cid, at):
    return (f"UPDATE competition SET status = 'OPEN', pool_built_at = {q(at)}, updated_at = {q(at)} "
            f"WHERE competition_id = {q(cid)} AND status = 'DRAFT';")


def pool_chunks(cid, max_bytes=MAX_BYTES):
    head = "INSERT INTO entry_number (competition_id, seq, entry_number, status, allocation_seq) VALUES "
    chunks, cur = [], []
    for seq in range(START_NUMBER, START_NUMBER + CAPACITY):
        row = f"({q(cid)}, {seq}, {q(entry_number(seq))}, 'AVAILABLE', 0)"
        if cur and len((head + ",".join(cur + [row]) + ";").encode()) > max_bytes:
            chunks.append(head + ",".join(cur) + ";")
            cur = []
        cur.append(row)
    if cur:
        chunks.append(head + ",".join(cur) + ";")
    return chunks


def build(cid, at):
    cid = str(cid)
    if not cid.isdigit():
        raise Refused("competition id must be numeric")
    if cid in RETIRED_COMPETITIONS:
        raise Refused(f"competition {cid} is a retired fixture ({RETIRED_COMPETITIONS[cid]})")
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{3})?Z", at):
        raise Refused("timestamp must be ISO-8601 UTC")
    files = [("00-competition.sql", competition_sql(cid, at))]
    files += [(f"{i + 1:02d}-pool.sql", c) for i, c in enumerate(pool_chunks(cid))]
    files.append(("99-open.sql", open_sql(cid, at)))
    for name, sql in files:
        if len(sql.encode()) >= D1_STATEMENT_LIMIT:
            raise Refused(f"{name} is {len(sql.encode())} bytes (D1 limit {D1_STATEMENT_LIMIT})")
    return files


def main(argv=None):
    p = argparse.ArgumentParser(prog="regsql.py")
    p.add_argument("cmd", choices=["write"]); p.add_argument("--competition", required=True); p.add_argument("--out", required=True)
    p.add_argument("--at", required=True)
    a = p.parse_args(argv)
    try:
        files = build(a.competition, a.at)
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
    os.makedirs(a.out, exist_ok=True)
    for name, sql in files:
        with open(os.path.join(a.out, name), "w") as f:
            f.write(sql + "\n")
        print(f"{name} {len(sql.encode())} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
