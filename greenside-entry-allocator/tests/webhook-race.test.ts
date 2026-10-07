/**
 * The concurrent first-delivery race (L1 Stage 4, 7 Oct 2026).
 *
 * Shopify delivers orders/create and orders/paid for the same order within
 * milliseconds. Live, order #1034's orders/create lost the ledger INSERT to
 * the concurrent orders/paid and the Worker answered 500:
 *
 *   webhook_failed  D1_ERROR: UNIQUE constraint failed: allocation.order_id, allocation.line_item_id
 *
 * These tests drive the real Worker fetch() with signed deliveries, a real
 * SQLite-backed D1 and only the Admin API mocked. A barrier on the product
 * read (which every first allocation makes AFTER reading the empty ledger)
 * guarantees that both deliveries have seen "no ledger row" before either
 * commits, so the race is reproduced deterministically rather than by luck.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { resetTokenSources } from '../src/auth';
import type { D1Like, D1StatementLike } from '../src/db';
import { computeHmac } from '../src/hmac';
import { allocationId } from '../src/idempotency';
import worker, { TOPIC_ROUTES, type Env } from '../src/index';
import { buildPool } from '../src/pool';
import { isLedgerIdentityConflict, processOrder } from '../src/process';
import { TestD1, seedAllocation, seedCompetition } from './helpers';
import { ORDER, SHOP, deps, mockFetch } from './order-fixtures';

const SECRET = 'test-webhook-secret';
const ORDER_ID = '1042';
const LINE_ID = '55';
const BODY = JSON.stringify({ id: 1042, admin_graphql_api_id: ORDER, name: '#1042' });
const OPTS = { quantity: 3, currentQuantity: 3, skillMode: 'none' } as const;
const STAGE4_MESSAGE =
  'D1_ERROR: UNIQUE constraint failed: allocation.order_id, allocation.line_item_id: SQLITE_CONSTRAINT (extended: SQLITE_CONSTRAINT_UNIQUE)';

let db: TestD1;
let logLines: string[];
let productGate: { n: number; waiting: Array<() => void> } | null;

function env(d1: D1Like = db): Env {
  return {
    DB: d1 as never,
    SHOPIFY_STORE: SHOP,
    SHOPIFY_CLIENT_ID: 'test-client-id',
    SHOPIFY_CLIENT_SECRET: 'test-client-secret-value',
    SHOPIFY_WEBHOOK_SECRET: SECRET,
    SHOPIFY_API_VERSION: '2026-07',
    ALLOWED_SHOP_DOMAIN: SHOP,
    DRY_RUN: 'false',
  };
}

async function deliver(topic: 'orders/create' | 'orders/paid', webhookId: string, d1?: D1Like): Promise<Response> {
  const route = TOPIC_ROUTES[topic]!;
  const request = new Request(`https://allocator.test${route.path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Shopify-Hmac-Sha256': await computeHmac(BODY, SECRET),
      'X-Shopify-Shop-Domain': SHOP,
      'X-Shopify-Topic': topic,
      'X-Shopify-Webhook-Id': webhookId,
    },
    body: BODY,
  });
  return worker.fetch(request, env(d1));
}

/** Holds the first `n` product reads until all `n` are waiting: every racer has read the empty ledger by then. */
function raceGate(n: number): void {
  productGate = { n, waiting: [] };
}

function logged(event: string): Array<Record<string, unknown>> {
  return logLines.map((l) => JSON.parse(l) as Record<string, unknown>).filter((l) => l['event'] === event);
}

/** Every invariant the race must not break, after any scenario. */
function expectOneCleanAllocation(units = 3): void {
  const allocs = db.query<{ allocation_id: string; held_count: number; target_count: number; status: string }>(
    'SELECT allocation_id, held_count, target_count, status FROM allocation',
  );
  expect(allocs).toHaveLength(1);
  expect(allocs[0]).toMatchObject({ held_count: units, target_count: units, status: 'ALLOCATED' });
  const numbers = db.query<{ entry_number: string; allocation_seq: number }>(
    "SELECT entry_number, allocation_seq FROM entry_number WHERE status = 'ALLOCATED' ORDER BY seq",
  );
  expect(numbers.map((n) => n.entry_number)).toEqual(Array.from({ length: units }, (_, i) => `PUT${1001 + i}`));
  expect(numbers.every((n) => n.allocation_seq === 1)).toBe(true); // each number issued exactly once
  expect(db.query("SELECT * FROM entry_event WHERE event_type = 'ALLOCATED'")).toHaveLength(units);
  expect(db.query('SELECT * FROM entry_event')).toHaveLength(units); // no other event of any kind
  expect(
    db.query(
      "SELECT competition_id, seq, allocation_seq FROM entry_event WHERE event_type = 'ALLOCATED' GROUP BY 1, 2, 3 HAVING COUNT(*) > 1",
    ),
  ).toEqual([]);
  expect(db.query('SELECT entry_number FROM entry_number GROUP BY entry_number HAVING COUNT(*) > 1')).toEqual([]);
  expect(
    db.query(
      "SELECT a.allocation_id FROM allocation a WHERE a.held_count <> (SELECT COUNT(*) FROM entry_number n WHERE n.allocation_id = a.allocation_id AND n.status = 'ALLOCATED')",
    ),
  ).toEqual([]);
}

beforeEach(async () => {
  db = new TestD1();
  seedCompetition(db, { capacity: 20 });
  await buildPool(db, '900001', { prefix: 'PUT', startNumber: 1001, capacity: 20, padWidth: 4 });
  logLines = [];
  productGate = null;
  vi.spyOn(console, 'log').mockImplementation((line: unknown) => {
    logLines.push(String(line));
  });
  resetTokenSources();
  const shopify = mockFetch(OPTS);
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    if (String(url) === `https://${SHOP}/admin/oauth/access_token`) {
      return Response.json({ access_token: 'shpat_test_access_token_value', scope: 'read_orders,read_products', expires_in: 86399 });
    }
    const body = JSON.parse(String(init?.body ?? '{}')) as { query: string };
    if (!body.query.includes('AllocatorOrder') && productGate) {
      const gate = productGate;
      await new Promise<void>((resolve) => {
        gate.waiting.push(resolve);
        if (gate.waiting.length >= gate.n) {
          productGate = null; // later product reads pass straight through
          for (const r of gate.waiting) r();
        }
      });
    }
    return shopify(url, init);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Stage 4 regression: orders/create loses the ledger INSERT to a concurrent orders/paid', () => {
  test('both deliveries answer 200, one allocation, the loser logs the resolved race (was: 500 UNIQUE allocation.order_id, allocation.line_item_id)', async () => {
    raceGate(2);
    const [paid, create] = await Promise.all([deliver('orders/paid', 'wh-paid-1034'), deliver('orders/create', 'wh-create-1034')]);

    expect([paid.status, create.status]).toEqual([200, 200]);
    expect(logged('webhook_failed')).toEqual([]);
    expect(logged('allocation_race_resolved')).toHaveLength(1);
    expect(logged('webhook_processed')).toHaveLength(2);
    expectOneCleanAllocation();
  });
});

describe('delivery order and timing', () => {
  test('1. orders/create then orders/paid, sequentially', async () => {
    const a = await deliver('orders/create', 'wh-c');
    const b = await deliver('orders/paid', 'wh-p');
    expect([a.status, b.status]).toEqual([200, 200]);
    const second = (await b.json()) as { status: string; outcomes: Array<{ action: string }> };
    expect(second).toMatchObject({ status: 'ok', outcomes: [{ action: 'NONE' }] });
    expect(logged('allocation_race_resolved')).toEqual([]);
    expectOneCleanAllocation();
  });

  test('2. orders/paid then orders/create, sequentially', async () => {
    const a = await deliver('orders/paid', 'wh-p');
    const b = await deliver('orders/create', 'wh-c');
    expect([a.status, b.status]).toEqual([200, 200]);
    expect(((await b.json()) as { outcomes: Array<{ action: string }> }).outcomes[0]!.action).toBe('NONE');
    expectOneCleanAllocation();
  });

  test('3. orders/create and orders/paid concurrently, both orders of arrival', async () => {
    raceGate(2);
    const r1 = await Promise.all([deliver('orders/create', 'wh-c'), deliver('orders/paid', 'wh-p')]);
    expect(r1.map((r) => r.status)).toEqual([200, 200]);
    expectOneCleanAllocation();
  });

  test('3b. natural interleaving without a barrier also never fails', async () => {
    const r = await Promise.all([deliver('orders/create', 'wh-c'), deliver('orders/paid', 'wh-p')]);
    expect(r.map((x) => x.status)).toEqual([200, 200]);
    expect(logged('webhook_failed')).toEqual([]);
    expectOneCleanAllocation();
  });

  test('4. two concurrent deliveries of the same webhook (same id, same topic)', async () => {
    raceGate(2);
    const r = await Promise.all([deliver('orders/paid', 'wh-same'), deliver('orders/paid', 'wh-same')]);
    expect(r.map((x) => x.status)).toEqual([200, 200]);
    expectOneCleanAllocation();
  });

  test('5. repeated delivery after a successful allocation changes nothing', async () => {
    await deliver('orders/paid', 'wh-p');
    const before = db.query('SELECT * FROM entry_event ORDER BY id');
    for (let i = 0; i < 5; i++) expect((await deliver('orders/paid', 'wh-p')).status).toBe(200);
    expect(db.query('SELECT * FROM entry_event ORDER BY id')).toEqual(before);
    expectOneCleanAllocation();
  });

  test('6. many different webhook ids for the same order line race together', async () => {
    raceGate(4);
    const r = await Promise.all([
      deliver('orders/create', 'wh-1'),
      deliver('orders/paid', 'wh-2'),
      deliver('orders/create', 'wh-3'),
      deliver('orders/paid', 'wh-4'),
    ]);
    expect(r.map((x) => x.status)).toEqual([200, 200, 200, 200]);
    expect(logged('allocation_race_resolved')).toHaveLength(3);
    expectOneCleanAllocation();
  });

  test('7. a later eligible delivery that finds the ledger row sends no ledger INSERT at all', async () => {
    await deliver('orders/paid', 'wh-p');
    const recorder = new RecordingD1(db);
    expect((await deliver('orders/create', 'wh-c', recorder)).status).toBe(200);
    expect(recorder.sql.some((s) => /INSERT INTO allocation/.test(s))).toBe(false);
    expectOneCleanAllocation();
  });

  test('12. the idempotent second delivery gets the ordinary success response', async () => {
    raceGate(2);
    const [a, b] = await Promise.all([deliver('orders/create', 'wh-c'), deliver('orders/paid', 'wh-p')]);
    const bodies = (await Promise.all([a.json(), b.json()])) as Array<{ status: string; outcomes: Array<{ action: string }> }>;
    expect(bodies.map((x) => x.status)).toEqual(['ok', 'ok']);
    expect(bodies.map((x) => x.outcomes[0]!.action).sort()).toEqual(['ALLOCATE', 'NONE']);
  });
});

/* ------------------------------------------------------------------ *
 * 8. Genuine failures are NOT turned into success.
 * ------------------------------------------------------------------ */

/** Records every statement's SQL; otherwise the real database. */
class RecordingD1 implements D1Like {
  readonly sql: string[] = [];
  constructor(protected readonly inner: TestD1) {}
  prepare(sql: string): D1StatementLike {
    this.sql.push(sql);
    return this.inner.prepare(sql);
  }
  batch(statements: D1StatementLike[]): Promise<unknown[]> {
    return this.inner.batch(statements);
  }
  exec(sql: string): Promise<unknown> {
    return this.inner.exec(sql);
  }
}

/**
 * Throws `message` from chosen batch calls. With `commitFirst`, the real batch
 * is committed before the throw (as if a concurrent delivery had won).
 */
class ScriptedD1 extends RecordingD1 {
  calls = 0;
  ledgerReads = 0;
  constructor(
    inner: TestD1,
    private readonly script: Array<{ message: string; commitFirst: boolean; before?: () => void } | null>,
    /** Ledger reads (SELECT ... WHERE allocation_id = ?1) that answer "no row", 1-based. */
    private readonly hideLedgerReads: number[] = [],
  ) {
    super(inner);
  }
  override prepare(sql: string): D1StatementLike {
    if (sql === 'SELECT * FROM allocation WHERE allocation_id = ?1' && this.hideLedgerReads.includes(++this.ledgerReads)) {
      const none: D1StatementLike = {
        bind: () => none,
        first: async () => null,
        all: async () => ({ results: [] }),
        run: async () => ({}),
      } as unknown as D1StatementLike;
      return none;
    }
    return super.prepare(sql);
  }
  override async batch(statements: D1StatementLike[]): Promise<unknown[]> {
    const step = this.script[this.calls++] ?? null;
    if (!step) return this.inner.batch(statements);
    if (step.commitFirst) await this.inner.batch(statements);
    step.before?.();
    throw new Error(step.message);
  }
}

describe('8. only the exact ledger-identity race is recognised', () => {
  test('8a. a row for the same order line under a DIFFERENT allocation_id is a genuine failure: 500, nothing claimed', async () => {
    seedAllocation(db, { allocationId: 'not-the-deterministic-id', competitionId: '900001', orderId: ORDER_ID, lineItemId: LINE_ID });
    const res = await deliver('orders/paid', 'wh-p');
    expect(res.status).toBe(500);
    expect(String(logged('webhook_failed')[0]?.['error'])).toMatch(/UNIQUE constraint failed: allocation\.order_id, allocation\.line_item_id/);
    expect(logged('allocation_race_resolved')).toEqual([]);
    expect(db.query("SELECT * FROM entry_number WHERE status = 'ALLOCATED'")).toEqual([]);
    expect(db.query('SELECT * FROM entry_event')).toEqual([]);
  });

  test('8b. a UNIQUE failure on ANOTHER table is rethrown even if the ledger row exists', async () => {
    const d1 = new ScriptedD1(db, [
      { message: 'D1_ERROR: UNIQUE constraint failed: entry_number.competition_id, entry_number.entry_number: SQLITE_CONSTRAINT', commitFirst: true },
    ]);
    await expect(processOrder(deps(d1, OPTS, 'x'), ORDER, 'ORDER_EDIT')).rejects.toThrow(/entry_number/);
    expect(d1.calls).toBe(1); // no re-run
  });

  test('8c. the ledger-identity message with NO committed row is rethrown', async () => {
    const d1 = new ScriptedD1(db, [{ message: STAGE4_MESSAGE, commitFirst: false }]);
    await expect(processOrder(deps(d1, OPTS, 'x'), ORDER, 'ORDER_EDIT')).rejects.toThrow(/allocation\.order_id/);
    expect(d1.calls).toBe(1);
    expect(db.query('SELECT * FROM allocation')).toEqual([]);
  });

  test('8d. the re-run happens at most once: a failure on the re-run is rethrown', async () => {
    const d1 = new ScriptedD1(db, [
      { message: STAGE4_MESSAGE, commitFirst: true },
      { message: STAGE4_MESSAGE, commitFirst: false },
    ]);
    await expect(processOrder(deps(d1, OPTS, 'x'), ORDER, 'ORDER_EDIT')).rejects.toThrow(/allocation\.order_id/);
    expect(d1.calls).toBe(2);
  });

  test('8e. the Stage 4 message itself, with the winner committed, is resolved by one re-run', async () => {
    const d1 = new ScriptedD1(db, [{ message: STAGE4_MESSAGE, commitFirst: true }]);
    const outcomes = await processOrder(deps(d1, OPTS, 'x'), ORDER, 'ORDER_EDIT');
    expect(outcomes.map((o) => o.action)).toEqual(['NONE']);
    expect(d1.calls).toBe(2);
    expectOneCleanAllocation();
  });

  test('8f. a non-UNIQUE error (constraint or transient) is never re-run', async () => {
    for (const message of ['D1_ERROR: NOT NULL constraint failed: entry_event.occurred_at', 'D1_ERROR: Network connection lost.']) {
      const d1 = new ScriptedD1(db, [{ message, commitFirst: false }]);
      await expect(processOrder(deps(d1, OPTS, 'x'), ORDER, 'ORDER_EDIT')).rejects.toThrow(message);
      expect(d1.calls).toBe(1);
    }
  });

  test('8i. a run that sent NO ledger INSERT (row already there) is never treated as the race', async () => {
    const id = await allocationId(SHOP, ORDER_ID, LINE_ID);
    seedAllocation(db, { allocationId: id, competitionId: '900001', orderId: ORDER_ID, lineItemId: LINE_ID, verdict: 'NOT_REQUIRED' });
    const d1 = new ScriptedD1(db, [{ message: STAGE4_MESSAGE, commitFirst: false }]);
    await expect(processOrder(deps(d1, OPTS, 'x'), ORDER, 'ORDER_EDIT')).rejects.toThrow(/allocation\.order_id/);
    expect(d1.calls).toBe(1);
  });

  test('8j. the re-run is bounded even if the ledger row seems to vanish and the race repeats', async () => {
    // Read 1: first pass sees no row. Read 2: the race check sees the winner's row. Read 3: the re-run is told
    // "no row" again, so it sends the INSERT again and loses again. Without the once-only guard it would loop.
    const d1 = new ScriptedD1(
      db,
      [{ message: STAGE4_MESSAGE, commitFirst: true }, { message: STAGE4_MESSAGE, commitFirst: false }, null],
      [3],
    );
    await expect(processOrder(deps(d1, OPTS, 'x'), ORDER, 'ORDER_EDIT')).rejects.toThrow(/allocation\.order_id/);
    expect(d1.calls).toBe(2);
  });

  for (const [field, value] of [['shop_domain', 'other.myshopify.com'], ['competition_id', '900002'], ['order_id', '9999'], ['line_item_id', '99']] as const) {
    test(`8k. a row under our allocation_id but a different ${field} is not our work: rethrown`, async () => {
      const id = await allocationId(SHOP, ORDER_ID, LINE_ID);
      const d1 = new ScriptedD1(db, [{
        message: STAGE4_MESSAGE,
        commitFirst: false,
        before: () => {
          seedAllocation(db, { allocationId: id, competitionId: '900001', orderId: ORDER_ID, lineItemId: LINE_ID, verdict: 'NOT_REQUIRED' });
          if (field === 'competition_id') {
            db.sqlite.exec('PRAGMA foreign_keys = OFF');
            db.sqlite.prepare(`UPDATE allocation SET competition_id = ? WHERE allocation_id = ?`).run(value, id);
            db.sqlite.exec('PRAGMA foreign_keys = ON');
          } else {
            db.sqlite.prepare(`UPDATE allocation SET ${field} = ? WHERE allocation_id = ?`).run(value, id);
          }
        },
      }]);
      await expect(processOrder(deps(d1, OPTS, 'x'), ORDER, 'ORDER_EDIT')).rejects.toThrow(/allocation\.order_id/);
      expect(d1.calls).toBe(1);
    });
  }

  test('8g. only the allocation table identity matches', () => {
    expect(isLedgerIdentityConflict(new Error(STAGE4_MESSAGE))).toBe(true);
    expect(isLedgerIdentityConflict(new Error('UNIQUE constraint failed: allocation.allocation_id'))).toBe(true);
    for (const m of [
      'UNIQUE constraint failed: entry_number.competition_id, entry_number.entry_number',
      'UNIQUE constraint failed: entry_event.allocation_id',
      'UNIQUE constraint failed: allocation.order_name',
      'UNIQUE constraint failed: allocation.order_id, allocation.line_item_id, allocation.extra',
      'UNIQUE constraint failed: allocation_archive.allocation_id',
      'NOT NULL constraint failed: allocation.order_id',
      'CHECK constraint failed: allocation',
    ]) {
      expect(isLedgerIdentityConflict(new Error(m))).toBe(false);
    }
  });

  test('8h. the deterministic id the recognition relies on is the one the ledger row carries', async () => {
    await deliver('orders/paid', 'wh-p');
    const id = await allocationId(SHOP, ORDER_ID, LINE_ID);
    expect(db.query('SELECT allocation_id FROM allocation')).toEqual([{ allocation_id: id }]);
  });
});
