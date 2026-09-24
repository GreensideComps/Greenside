import { beforeEach, describe, expect, test } from 'vitest';
import { claimLowest, countHeld, listHeld } from '../src/allocate';
import { converge, planConvergence, targetCount } from '../src/converge';
import { buildPool } from '../src/pool';
import { EVENT_CONTEXT, NOW, TestD1, seedCompetition } from './helpers';

let db: TestD1;
let competitionId: string;

beforeEach(async () => {
  db = new TestD1();
  competitionId = seedCompetition(db, { capacity: 20 }).competitionId;
  await buildPool(db, competitionId, { prefix: 'PUT', startNumber: 1001, capacity: 20, padWidth: 4 });
});

const seed = (allocationId: string, count: number) =>
  claimLowest({ db, competitionId, allocationId, count, now: NOW, orderId: 'o1', lineItemId: 'l1', customerRef: null });

const run = (allocationId: string, currentQuantity: number, entriesPerUnit = 1, status = 'OPEN') =>
  converge({
    db, competitionId, competitionStatus: status, allocationId,
    currentQuantity, entriesPerUnit, now: NOW, reason: 'REFUND',
    orderId: 'o1', lineItemId: 'l1', customerRef: null, ...EVENT_CONTEXT,
  });

describe('the target rule', () => {
  test('target = currentQuantity x entries_per_unit', () => {
    expect(targetCount(5, 1)).toBe(5);
    expect(targetCount(2, 5)).toBe(10);
    expect(targetCount(0, 25)).toBe(0);
  });

  test('a negative quantity floors at zero and entries_per_unit floors at one', () => {
    expect(targetCount(-3, 1)).toBe(0);
    expect(targetCount(3, 0)).toBe(3);
  });

  test('the plan compares held against target', () => {
    expect(planConvergence(5, 5)).toMatchObject({ action: 'NONE', delta: 0 });
    expect(planConvergence(3, 5)).toMatchObject({ action: 'RELEASE', delta: 2 });
    expect(planConvergence(5, 3)).toMatchObject({ action: 'ALLOCATE', delta: 2 });
  });
});

describe('full refund', () => {
  test('currentQuantity 0 releases every number', async () => {
    await seed('a1', 3);
    const out = await run('a1', 0);
    expect(out.plan).toMatchObject({ target: 0, held: 3, action: 'RELEASE', delta: 3 });
    expect(out.released.map((r) => r.entry_number)).toEqual(['PUT1003', 'PUT1002', 'PUT1001']);
    expect(await countHeld(db, competitionId, 'a1')).toBe(0);
  });

  test('released numbers return to the pool while OPEN', async () => {
    await seed('a1', 3);
    await run('a1', 0);
    const available = db.query<{ n: number }>(`SELECT COUNT(*) AS n FROM entry_number WHERE status='AVAILABLE'`)[0];
    expect(available).toMatchObject({ n: 20 });
  });
});

describe('partial refund', () => {
  test('quantity 5 refunded by 2 leaves 3, releasing the HIGHEST two', async () => {
    // This is the exact case from the brief. Note the quantity source: it is
    // currentQuantity, which Shopify documents as excluding refunded units.
    await seed('a1', 5);
    const out = await run('a1', 3);
    expect(out.plan).toMatchObject({ target: 3, held: 5, action: 'RELEASE', delta: 2 });
    expect(out.released.map((r) => r.entry_number)).toEqual(['PUT1005', 'PUT1004']);
    expect((await listHeld(db, competitionId, 'a1')).map((h) => h.entry_number)).toEqual([
      'PUT1001', 'PUT1002', 'PUT1003',
    ]);
  });

  test('bundle mode multiplies: 2 units of a 5-entry bundle refunded by 1 leaves 5', async () => {
    await seed('a1', 10); // 2 units x 5 entries
    const out = await run('a1', 1, 5);
    expect(out.plan).toMatchObject({ target: 5, held: 10, action: 'RELEASE', delta: 5 });
    expect(await countHeld(db, competitionId, 'a1')).toBe(5);
  });
});

describe('money-only refund', () => {
  test('an unchanged currentQuantity releases NOTHING', async () => {
    // A goodwill refund carries no refundLineItems, so currentQuantity does
    // not move. No special case is needed: the target simply does not change.
    await seed('a1', 3);
    const out = await run('a1', 3);
    expect(out.plan.action).toBe('NONE');
    expect(out.released).toEqual([]);
    expect(await countHeld(db, competitionId, 'a1')).toBe(3);
  });
});

describe('cancellation', () => {
  test('a cancelled order releases everything, exactly like a full refund', async () => {
    await seed('a1', 4);
    const out = await converge({
      db, competitionId, competitionStatus: 'OPEN', allocationId: 'a1',
      currentQuantity: 0, entriesPerUnit: 1, now: NOW, reason: 'CANCELLED',
      orderId: 'o1', lineItemId: 'l1', customerRef: null, ...EVENT_CONTEXT,
    });
    expect(out.released).toHaveLength(4);
    expect(db.query<{ release_reason: string }>(`SELECT release_reason FROM entry_number WHERE seq=1001`)[0]).toMatchObject({
      release_reason: 'CANCELLED',
    });
  });
});

describe('order edit', () => {
  test('an increased quantity allocates the difference at the lowest free numbers', async () => {
    await seed('a1', 2);
    await seed('a2', 1); // takes PUT1003
    const out = await run('a1', 4);
    expect(out.plan).toMatchObject({ target: 4, held: 2, action: 'ALLOCATE', delta: 2 });
    expect(out.claimed.map((c) => c.entry_number)).toEqual(['PUT1004', 'PUT1005']);
    expect(await countHeld(db, competitionId, 'a1')).toBe(4);
  });

  test('a decreased quantity releases from the top', async () => {
    await seed('a1', 4);
    const out = await run('a1', 1);
    expect(out.released.map((r) => r.entry_number)).toEqual(['PUT1004', 'PUT1003', 'PUT1002']);
  });
});

describe('idempotency', () => {
  test('converging repeatedly on the same target changes nothing after the first pass', async () => {
    await seed('a1', 5);
    await run('a1', 3);
    for (let i = 0; i < 5; i++) {
      const out = await run('a1', 3);
      expect(out.plan.action).toBe('NONE');
      expect(out.released).toEqual([]);
      expect(out.claimed).toEqual([]);
    }
    expect(await countHeld(db, competitionId, 'a1')).toBe(3);
  });

  test('order independence: a refund converged before a re-run still ends correct', async () => {
    await seed('a1', 5);
    await run('a1', 2); // refund processed first
    await run('a1', 2); // the "paid" event arrives afterwards
    expect(await countHeld(db, competitionId, 'a1')).toBe(2);
  });
});

describe('freeze interaction', () => {
  test('while FROZEN a release does NOT return the number to the pool', async () => {
    await seed('a1', 3);
    // The release reads the competition's status itself, so it must really be FROZEN.
    db.sqlite.exec(`UPDATE competition SET status = 'FROZEN', frozen_at = '${NOW}'`);
    const out = await converge({
      db, competitionId, competitionStatus: 'FROZEN', allocationId: 'a1',
      currentQuantity: 0, entriesPerUnit: 1, now: NOW, reason: 'REFUND',
      orderId: 'o1', lineItemId: 'l1', customerRef: null, ...EVENT_CONTEXT,
    });
    expect(out.released).toHaveLength(3);
    const statuses = db.query<{ status: string }>(`SELECT status FROM entry_number WHERE seq IN (1001,1002,1003)`);
    expect(statuses.every((s) => s.status === 'RELEASED')).toBe(true);
  });

  test('while FROZEN no additional numbers are allocated', async () => {
    await seed('a1', 1);
    const out = await converge({
      db, competitionId, competitionStatus: 'FROZEN', allocationId: 'a1',
      currentQuantity: 5, entriesPerUnit: 1, now: NOW, reason: 'ORDER_EDIT',
      orderId: 'o1', lineItemId: 'l1', customerRef: null, ...EVENT_CONTEXT,
    });
    expect(out.plan.action).toBe('ALLOCATE');
    expect(out.claimed).toEqual([]); // refused: the entry list is final
    expect(await countHeld(db, competitionId, 'a1')).toBe(1);
  });
});

describe('capacity refusal during convergence', () => {
  test('an increase beyond the pool is reported, not partially filled', async () => {
    await seed('a1', 18);
    const out = await run('a1', 25);
    expect(out.refusedCapacity).toBe(true);
    expect(await countHeld(db, competitionId, 'a1')).toBe(18);
  });
});
