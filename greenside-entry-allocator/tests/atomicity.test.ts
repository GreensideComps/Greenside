/**
 * Atomicity of convergence under injected failure, repeated delivery and
 * concurrency.
 *
 * The failure this guards against was reproduced by fault injection: the
 * claim (or release) committed, the process died before every ALLOCATED (or
 * RELEASED / RETURNED_TO_POOL) event was written, and the retry -- finding
 * held == target -- never wrote them. Numbers and ledger healed; the
 * append-only history kept a permanent hole. The skill event had the same
 * hole, because the retry never re-creates an existing ledger row.
 *
 * Every write is now one batch. The fault injector below fails the k-th write
 * of a run for every k: inside the batch it swaps that statement for one that
 * fails IN SQL, so the real SQLite transaction aborts after the earlier
 * statements have executed; outside the batch it throws before the write.
 */
import { beforeEach, describe, expect, test } from 'vitest';
import { countHeld } from '../src/allocate';
import { converge } from '../src/converge';
import type { D1Like, D1StatementLike } from '../src/db';
import { freezeBlockers } from '../src/freeze';
import { allocationId } from '../src/idempotency';
import { buildPool } from '../src/pool';
import { processOrder } from '../src/process';
import { EVENT_CONTEXT, NOW, TestD1, seedAllocation, seedCompetition } from './helpers';
import { ORDER, SHOP, deps, type OrderOpts } from './order-fixtures';

/* ------------------------------------------------------------------ *
 * Fault injection
 * ------------------------------------------------------------------ */

const FAILING_SQL = `INSERT INTO entry_event (occurred_at) VALUES ('injected failure')`; // NOT NULL violation
const isWrite = (sql: string) => /^\s*(INSERT|UPDATE|DELETE)\b/i.test(sql);

interface Tracked extends D1StatementLike { sql: string; inner: D1StatementLike }

/** Fails the `failAt`-th write of a run; records every write it sees. */
class FaultyD1 implements D1Like {
  readonly writes: string[] = [];
  private n = 0;
  constructor(private readonly db: TestD1, private readonly failAt = -1) {}

  private next(label: string): boolean {
    this.writes.push(label);
    return this.n++ === this.failAt;
  }

  prepare(sql: string): D1StatementLike {
    let inner = this.db.prepare(sql);
    const guard = () => {
      if (isWrite(sql) && this.next(`direct: ${label(sql)}`)) throw new Error('injected failure');
    };
    const tracked: Tracked = {
      sql,
      get inner() { return inner; },
      bind: (...v: unknown[]) => { inner = inner.bind(...v); return tracked; },
      all: async <T>() => { guard(); return inner.all<T>(); },
      first: async <T>() => { guard(); return inner.first<T>(); },
      run: async () => { guard(); return inner.run(); },
    } as Tracked;
    return tracked;
  }

  batch(statements: D1StatementLike[]): Promise<unknown[]> {
    const real = (statements as Tracked[]).map((s) =>
      isWrite(s.sql) && this.next(`batch: ${label(s.sql)}`) ? this.db.prepare(FAILING_SQL) : s.inner,
    );
    return this.db.batch(real);
  }

  exec(sql: string): Promise<unknown> {
    return this.db.exec(sql);
  }
}

function label(sql: string): string {
  const s = sql.replace(/\s+/g, ' ').trim();
  const m = /^(INSERT INTO \w+|UPDATE \w+)/i.exec(s);
  const what = m?.[1] ?? s.slice(0, 30);
  if (/'ALLOCATED', 'AVAILABLE'/.test(s)) return `${what} [ALLOCATED events]`;
  if (/'RELEASED', 'ALLOCATED'/.test(s)) return `${what} [RELEASED events]`;
  if (/'RETURNED_TO_POOL'/.test(s)) return `${what} [RETURNED_TO_POOL events]`;
  if (/SET status = 'ALLOCATED'/.test(s)) return `${what} [claim]`;
  if (/SET status = CASE/.test(s)) return `${what} [release]`;
  if (/^UPDATE allocation/i.test(s)) return `${what} [counts]`;
  return what;
}

/* ------------------------------------------------------------------ *
 * What "correct" means
 * ------------------------------------------------------------------ */

/** Everything a run decides, minus what legitimately differs per attempt (ids, run ids, clocks). */
function snapshot(db: TestD1): string {
  return JSON.stringify({
    pool: db.query(`SELECT seq, status, allocation_id, allocation_seq, release_reason FROM entry_number ORDER BY seq`),
    ledger: db.query(`SELECT allocation_id, target_count, held_count, status, skill_verdict FROM allocation ORDER BY allocation_id`),
    events: db.query(
      `SELECT event_type, seq, allocation_seq, allocation_id, from_status, to_status, order_id, reason, detail_json
         FROM entry_event WHERE event_type NOT IN ('REFUSED_CAPACITY', 'ORDER_HELD')
        ORDER BY event_type, seq, allocation_seq`,
    ),
  });
}

function duplicateNumberEvents(db: TestD1): number {
  return db.query(
    `SELECT seq, allocation_seq, event_type FROM entry_event
      WHERE event_type IN ('ALLOCATED', 'RELEASED', 'RETURNED_TO_POOL')
      GROUP BY competition_id, seq, allocation_seq, event_type HAVING COUNT(*) > 1`,
  ).length;
}

async function auditBlockers(db: TestD1, status: string): Promise<string[]> {
  const blockers = await freezeBlockers({ db, competitionId: '900001', competitionStatus: status, expectedCapacity: 20, configValid: true });
  return blockers.map((b) => b.code).filter((c) => c === 'AUDIT_GAP' || c === 'ALLOCATION_LEDGER_DRIFT' || c === 'ORPHANED_CLAIMS');
}

/* ------------------------------------------------------------------ *
 * The matrix
 * ------------------------------------------------------------------ */

interface Scenario {
  setup: OrderOpts[];
  step: OrderOpts;
  reason: 'ORDER_EDIT' | 'REFUND' | 'CANCELLED';
  freeze?: boolean;
  capacity?: number;
  /** The writes the step performs, in order. Pins the batch shape. */
  writes: string[];
}

const LEDGER = 'batch: INSERT INTO allocation';
const SKILL = 'batch: INSERT INTO entry_event';
const CLAIM = 'batch: UPDATE entry_number [claim]';
const RELEASE = 'batch: UPDATE entry_number [release]';
const EVENTS = [
  'batch: INSERT INTO entry_event [ALLOCATED events]',
  'batch: INSERT INTO entry_event [RELEASED events]',
  'batch: INSERT INTO entry_event [RETURNED_TO_POOL events]',
];
const COUNTS = 'batch: UPDATE allocation [counts]';

const SCENARIOS: Record<string, Scenario> = {
  'allocate 3': {
    setup: [], step: { quantity: 3, currentQuantity: 3 }, reason: 'ORDER_EDIT',
    writes: [LEDGER, CLAIM, ...EVENTS, COUNTS],
  },
  'allocate 3 with a wrong skill answer': {
    setup: [], step: { quantity: 3, currentQuantity: 3, answer: 'Rough' }, reason: 'ORDER_EDIT',
    writes: [LEDGER, SKILL, CLAIM, ...EVENTS, COUNTS],
  },
  'allocate 3 with no correct answer configured (UNJUDGED)': {
    setup: [], step: { quantity: 3, currentQuantity: 3, correct: null }, reason: 'ORDER_EDIT',
    writes: [LEDGER, SKILL, CLAIM, ...EVENTS, COUNTS],
  },
  'order edit 3 -> 5': {
    setup: [{ quantity: 3, currentQuantity: 3 }], step: { quantity: 5, currentQuantity: 5 }, reason: 'ORDER_EDIT',
    writes: [CLAIM, ...EVENTS, COUNTS],
  },
  'partial refund 3 -> 1 while OPEN': {
    setup: [{ quantity: 3, currentQuantity: 3 }], step: { quantity: 3, currentQuantity: 1 }, reason: 'REFUND',
    writes: [RELEASE, ...EVENTS, COUNTS],
  },
  'cancellation 3 -> 0 while OPEN': {
    setup: [{ quantity: 3, currentQuantity: 3 }], step: { quantity: 3, currentQuantity: 0, cancelledAt: NOW }, reason: 'CANCELLED',
    writes: [RELEASE, ...EVENTS, COUNTS],
  },
  'partial refund 3 -> 1 after FROZEN': {
    setup: [{ quantity: 3, currentQuantity: 3 }], step: { quantity: 3, currentQuantity: 1 }, reason: 'REFUND', freeze: true,
    writes: [RELEASE, ...EVENTS, COUNTS],
  },
  'bundle: 2 units x 2 entries': {
    setup: [], step: { quantity: 2, currentQuantity: 2, entries: '2' }, reason: 'ORDER_EDIT',
    writes: [LEDGER, CLAIM, ...EVENTS, COUNTS],
  },
  'bundle: 2 units x 2 entries, one unit refunded': {
    setup: [{ quantity: 2, currentQuantity: 2, entries: '2' }], step: { quantity: 2, currentQuantity: 1, entries: '2' }, reason: 'REFUND',
    writes: [RELEASE, ...EVENTS, COUNTS],
  },
  'capacity refused: 5 wanted, 4 in the pool': {
    setup: [], step: { quantity: 5, currentQuantity: 5 }, reason: 'ORDER_EDIT', capacity: 4,
    writes: [LEDGER, CLAIM, ...EVENTS, COUNTS, 'direct: INSERT INTO entry_event'],
  },
};

async function prepared(sc: Scenario): Promise<TestD1> {
  const db = new TestD1();
  const capacity = sc.capacity ?? 20;
  seedCompetition(db, { capacity });
  await buildPool(db, '900001', { prefix: 'PUT', startNumber: 1001, capacity, padWidth: 4 });
  for (const [i, s] of sc.setup.entries()) await processOrder(deps(db, s, `setup-${i}`), ORDER, 'ORDER_EDIT');
  if (sc.freeze) db.sqlite.exec(`UPDATE competition SET status = 'FROZEN', frozen_at = '${NOW}'`);
  return db;
}

describe('fault injection: every write of every scenario', () => {
  for (const [name, sc] of Object.entries(SCENARIOS)) {
    test(name, async () => {
      // The reference: the same step with nothing going wrong.
      const clean = await prepared(sc);
      const probe = new FaultyD1(clean);
      await processOrder(deps(probe, sc.step, 'clean'), ORDER, sc.reason);
      expect(probe.writes).toEqual(sc.writes);
      const expected = snapshot(clean);
      expect(duplicateNumberEvents(clean)).toBe(0);
      expect(await auditBlockers(clean, sc.freeze ? 'FROZEN' : 'OPEN')).toEqual([]);

      for (let k = 0; k < sc.writes.length; k++) {
        const db = await prepared(sc);
        const before = snapshot(db);

        await expect(processOrder(deps(new FaultyD1(db, k), sc.step, 'failed'), ORDER, sc.reason)).rejects.toThrow();

        // No partial state survives a failed batch.
        if (sc.writes[k]!.startsWith('batch:')) expect(snapshot(db), `failure at write ${k}`).toBe(before);

        // Retry converges -- and a redelivery after that changes nothing.
        await processOrder(deps(db, sc.step, 'retry'), ORDER, sc.reason);
        await processOrder(deps(db, sc.step, 'redelivery'), ORDER, sc.reason);
        expect(snapshot(db), `failure at write ${k}`).toBe(expected);
        expect(duplicateNumberEvents(db)).toBe(0);
        expect(await auditBlockers(db, sc.freeze ? 'FROZEN' : 'OPEN')).toEqual([]);
      }
    });
  }

  test('the matrix covers every write point of every scenario', () => {
    const points = Object.values(SCENARIOS).reduce((n, sc) => n + sc.writes.length, 0);
    expect(points).toBe(58);
  });
});

describe('the history a failed-then-retried run leaves', () => {
  test('a crash between the claim and its events leaves no numbers held and no events', async () => {
    const sc = SCENARIOS['allocate 3']!;
    const db = await prepared(sc);
    // Fail the ALLOCATED-events statement: under the old code the claim had
    // already committed at this point and the events were lost for good.
    await expect(processOrder(deps(new FaultyD1(db, 2), sc.step, 'failed'), ORDER, sc.reason)).rejects.toThrow();
    expect(db.query(`SELECT * FROM entry_number WHERE status <> 'AVAILABLE'`)).toEqual([]);
    expect(db.query(`SELECT * FROM allocation`)).toEqual([]);
    expect(db.query(`SELECT * FROM entry_event`)).toEqual([]);

    await processOrder(deps(db, sc.step, 'retry'), ORDER, sc.reason);
    expect(db.query<{ event_type: string; seq: number; run_id: string }>(
      `SELECT event_type, seq, run_id FROM entry_event ORDER BY id`,
    )).toEqual([
      { event_type: 'ALLOCATED', seq: 1001, run_id: 'retry' },
      { event_type: 'ALLOCATED', seq: 1002, run_id: 'retry' },
      { event_type: 'ALLOCATED', seq: 1003, run_id: 'retry' },
    ]);
  });

  test('a crash between the release and its events leaves every number still held', async () => {
    const sc = SCENARIOS['partial refund 3 -> 1 while OPEN']!;
    const db = await prepared(sc);
    await expect(processOrder(deps(new FaultyD1(db, 2), sc.step, 'failed'), ORDER, sc.reason)).rejects.toThrow();
    const key = await allocationId(SHOP, '1042', '55');
    expect(await countHeld(db, '900001', key)).toBe(3);
    expect(db.query(`SELECT * FROM entry_event WHERE event_type <> 'ALLOCATED'`)).toEqual([]);
  });

  test('the missing skill event is written with its ledger row, or not at all', async () => {
    const sc = SCENARIOS['allocate 3 with a wrong skill answer']!;
    const db = await prepared(sc);
    // Fail the claim, which comes AFTER the ledger row and the skill event.
    await expect(processOrder(deps(new FaultyD1(db, 2), sc.step, 'failed'), ORDER, sc.reason)).rejects.toThrow();
    expect(db.query(`SELECT * FROM allocation`)).toEqual([]);
    expect(db.query(`SELECT * FROM entry_event`)).toEqual([]);
    await processOrder(deps(db, sc.step, 'retry'), ORDER, sc.reason);
    expect(db.query(`SELECT event_type FROM entry_event WHERE event_type = 'INCORRECT_SKILL'`)).toHaveLength(1);
  });
});

describe('repeated webhook delivery', () => {
  test('five deliveries of the paid order write each event once', async () => {
    const db = await prepared({ setup: [], step: { quantity: 3, currentQuantity: 3 }, reason: 'ORDER_EDIT', writes: [] });
    for (let i = 0; i < 5; i++) await processOrder(deps(db, { quantity: 3, currentQuantity: 3, answer: 'Rough' }, `d${i}`), ORDER, 'ORDER_EDIT');
    expect(db.query(`SELECT event_type, seq FROM entry_event ORDER BY id`)).toEqual([
      { event_type: 'INCORRECT_SKILL', seq: null },
      { event_type: 'ALLOCATED', seq: 1001 },
      { event_type: 'ALLOCATED', seq: 1002 },
      { event_type: 'ALLOCATED', seq: 1003 },
    ]);
  });

  test('three deliveries of the same refund release once and record once', async () => {
    const db = await prepared({ setup: [{ quantity: 3, currentQuantity: 3 }], step: { quantity: 3, currentQuantity: 1 }, reason: 'REFUND', writes: [] });
    for (let i = 0; i < 3; i++) await processOrder(deps(db, { quantity: 3, currentQuantity: 1 }, `r${i}`), ORDER, 'REFUND');
    expect(db.query(`SELECT event_type, seq FROM entry_event WHERE event_type <> 'ALLOCATED' ORDER BY id`)).toEqual([
      { event_type: 'RELEASED', seq: 1003 },
      { event_type: 'RELEASED', seq: 1002 },
      { event_type: 'RETURNED_TO_POOL', seq: 1003 },
      { event_type: 'RETURNED_TO_POOL', seq: 1002 },
    ]);
    expect(duplicateNumberEvents(db)).toBe(0);
  });
});

/* ------------------------------------------------------------------ *
 * Concurrency
 * ------------------------------------------------------------------ */

describe('concurrent convergence of the same allocation', () => {
  let db: TestD1;
  beforeEach(async () => {
    db = new TestD1();
    seedCompetition(db, { capacity: 20 });
    await buildPool(db, '900001', { prefix: 'PUT', startNumber: 1001, capacity: 20, padWidth: 4 });
    seedAllocation(db, { allocationId: 'A', competitionId: '900001', orderId: 'o', lineItemId: 'l' });
  });

  const run = (currentQuantity: number, reason: 'ORDER_EDIT' | 'REFUND' = 'ORDER_EDIT') =>
    converge({
      db, competitionId: '900001', competitionStatus: 'OPEN', allocationId: 'A', currentQuantity, entriesPerUnit: 1,
      now: NOW, reason, orderId: 'o', lineItemId: 'l', customerRef: null, ...EVENT_CONTEXT,
    });

  test('two runs that both read held = 0 claim the target once, not twice', async () => {
    // Held 6 against a target of 3 before the count moved into SQL.
    await Promise.all([run(3), run(3)]);
    expect(await countHeld(db, '900001', 'A')).toBe(3);
  });

  test('ten concurrent runs up, then ten down: no over-claim, no over-release, one event per change', async () => {
    const up = await Promise.all(Array.from({ length: 10 }, () => run(5)));
    expect(await countHeld(db, '900001', 'A')).toBe(5);
    expect(up.flatMap((o) => o.claimed)).toHaveLength(5);
    expect(up.some((o) => o.refusedCapacity)).toBe(false); // a lost race is not a capacity refusal

    const down = await Promise.all(Array.from({ length: 10 }, () => run(2, 'REFUND')));
    expect(await countHeld(db, '900001', 'A')).toBe(2);
    expect(down.flatMap((o) => o.released).map((r) => r.entry_number).sort()).toEqual(['PUT1003', 'PUT1004', 'PUT1005']);

    const count = (type: string) => db.query(`SELECT * FROM entry_event WHERE event_type = ?`, type).length;
    expect([count('ALLOCATED'), count('RELEASED'), count('RETURNED_TO_POOL')]).toEqual([5, 3, 3]);
    expect(duplicateNumberEvents(db)).toBe(0);
    expect(db.query(`SELECT held_count, target_count, status FROM allocation`)).toEqual([
      { held_count: 2, target_count: 2, status: 'ALLOCATED' },
    ]);
  });

  test('racing allocations for the last numbers: all-or-nothing, never beyond capacity', async () => {
    for (let i = 0; i < 15; i++) seedAllocation(db, { allocationId: `c${i}`, competitionId: '900001', orderId: `o${i}`, lineItemId: `l${i}` });
    const outcomes = await Promise.all(
      Array.from({ length: 15 }, (_, i) =>
        converge({
          db, competitionId: '900001', competitionStatus: 'OPEN', allocationId: `c${i}`, currentQuantity: 2, entriesPerUnit: 1,
          now: NOW, reason: 'ORDER_EDIT', orderId: `o${i}`, lineItemId: `l${i}`, customerRef: null, ...EVENT_CONTEXT,
        }),
      ),
    );
    expect(outcomes.filter((o) => o.claimed.length === 2)).toHaveLength(10);
    expect(outcomes.filter((o) => o.refusedCapacity)).toHaveLength(5);
    expect(outcomes.every((o) => o.claimed.length === 0 || o.claimed.length === 2)).toBe(true);
    expect(db.query(`SELECT * FROM entry_number WHERE status = 'ALLOCATED'`)).toHaveLength(20);
    expect(db.query(`SELECT * FROM entry_event WHERE event_type = 'ALLOCATED'`)).toHaveLength(20);
  });
});

describe('concurrent first delivery of the same order', () => {
  test('one ledger row, one set of numbers, one set of events; the losers fail cleanly and a retry is a no-op', async () => {
    const db = new TestD1();
    seedCompetition(db, { capacity: 20 });
    await buildPool(db, '900001', { prefix: 'PUT', startNumber: 1001, capacity: 20, padWidth: 4 });
    const step = { quantity: 3, currentQuantity: 3, answer: 'Rough' };

    const results = await Promise.allSettled(Array.from({ length: 5 }, (_, i) => processOrder(deps(db, step, `c${i}`), ORDER, 'ORDER_EDIT')));
    expect(results.filter((r) => r.status === 'fulfilled').length).toBeGreaterThanOrEqual(1);
    for (const r of results) if (r.status === 'rejected') expect(String(r.reason)).toMatch(/UNIQUE constraint failed: allocation/);

    const check = () => {
      expect(db.query(`SELECT * FROM allocation`)).toHaveLength(1);
      expect(db.query(`SELECT * FROM entry_number WHERE status = 'ALLOCATED'`)).toHaveLength(3);
      expect(db.query(`SELECT event_type FROM entry_event ORDER BY id`).map((e) => (e as { event_type: string }).event_type)).toEqual([
        'INCORRECT_SKILL', 'ALLOCATED', 'ALLOCATED', 'ALLOCATED',
      ]);
    };
    check();
    await processOrder(deps(db, step, 'retry'), ORDER, 'ORDER_EDIT');
    check();
  });
});
