import { beforeEach, describe, expect, test } from 'vitest';
import { claimLowest, countHeld, listHeld, releaseHighest } from '../src/allocate';
import { buildPool } from '../src/pool';
import type { NumberingConfig } from '../src/numbering';
import { NOW, TestD1, seedCompetition } from './helpers';

let db: TestD1;
let competitionId: string;
const SMALL: NumberingConfig = { prefix: 'PUT', startNumber: 1001, capacity: 4, padWidth: 4 };

beforeEach(async () => {
  db = new TestD1();
  competitionId = seedCompetition(db, { capacity: 4 }).competitionId;
  await buildPool(db, competitionId, SMALL);
});

function claim(allocationId: string, count: number) {
  return claimLowest({
    db, competitionId, allocationId, count, now: NOW,
    orderId: 'o1', lineItemId: 'l1', customerRef: 'c1',
  });
}

describe('lowest available first', () => {
  test('the specified example: pool of 4, first claim of 2 gets PUT1001 and PUT1002', async () => {
    const first = await claim('a1', 2);
    expect(first.ok && first.claimed.map((c) => c.entry_number)).toEqual(['PUT1001', 'PUT1002']);
  });

  test('the next claim continues from PUT1003', async () => {
    await claim('a1', 2);
    const second = await claim('a2', 2);
    expect(second.ok && second.claimed.map((c) => c.entry_number)).toEqual(['PUT1003', 'PUT1004']);
  });

  test('a released number is reissued ahead of higher free numbers', async () => {
    // a1 takes 1001-1002, a2 takes 1003.
    await claim('a1', 2);
    await claim('a2', 1);
    // a1 is refunded: 1001 and 1002 return to the pool.
    await releaseHighest({ db, competitionId, allocationId: 'a1', count: 2, now: NOW, reason: 'REFUND', returnToPool: true });
    // The next customer must receive the LOWEST available, which is now 1001.
    const next = await claim('a3', 2);
    expect(next.ok && next.claimed.map((c) => c.entry_number)).toEqual(['PUT1001', 'PUT1002']);
  });

  test('the brief\'s worked example end to end', async () => {
    const big = new TestD1();
    const id = seedCompetition(big, { capacity: 100 }).competitionId;
    await buildPool(big, id, { prefix: 'PUT', startNumber: 1001, capacity: 100, padWidth: 4 });
    const A = await claimLowest({ db: big, competitionId: id, allocationId: 'A', count: 3, now: NOW, orderId: '1042', lineItemId: 'l', customerRef: 'a' });
    expect(A.ok && A.claimed.map((c) => c.entry_number)).toEqual(['PUT1001', 'PUT1002', 'PUT1003']);

    await releaseHighest({ db: big, competitionId: id, allocationId: 'A', count: 3, now: NOW, reason: 'REFUND', returnToPool: true });

    const B = await claimLowest({ db: big, competitionId: id, allocationId: 'B', count: 2, now: NOW, orderId: '1057', lineItemId: 'l', customerRef: 'b' });
    expect(B.ok && B.claimed.map((c) => c.entry_number)).toEqual(['PUT1001', 'PUT1002']);
  });
});

describe('no over-allocation', () => {
  test('claiming more than are available is refused entirely, not partially', async () => {
    const result = await claim('a1', 5); // pool holds 4
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.code).toBe('REFUSED_CAPACITY');
    expect(result.available).toBe(4);
    // Nothing was taken: all four are still free.
    expect(db.query(`SELECT COUNT(*) AS n FROM entry_number WHERE status='AVAILABLE'`)[0]).toMatchObject({ n: 4 });
  });

  test('claiming exactly the remaining number of entries succeeds', async () => {
    const result = await claim('a1', 4);
    expect(result.ok && result.claimed).toHaveLength(4);
  });

  test('capacity can never be exceeded across several claims', async () => {
    await claim('a1', 3);
    const overflow = await claim('a2', 2); // only 1 left
    expect(overflow.ok).toBe(false);
    expect(await countHeld(db, competitionId, 'a2')).toBe(0);
  });

  test('a claim of zero is a no-op', async () => {
    const result = await claim('a1', 0);
    expect(result.ok && result.claimed).toEqual([]);
  });
});

describe('no duplicate allocation', () => {
  test('two allocations never share a number', async () => {
    await claim('a1', 2);
    await claim('a2', 2);
    const rows = db.query<{ entry_number: string; allocation_id: string }>(
      `SELECT entry_number, allocation_id FROM entry_number WHERE status='ALLOCATED'`,
    );
    expect(new Set(rows.map((r) => r.entry_number)).size).toBe(4);
  });

  test('allocation_id is stamped by the claiming statement itself', async () => {
    await claim('a1', 2);
    const held = await listHeld(db, competitionId, 'a1');
    expect(held.map((h) => h.entry_number)).toEqual(['PUT1001', 'PUT1002']);
  });

  test('allocation_seq increments each time a number is issued', async () => {
    const first = await claim('a1', 1);
    expect(first.ok && first.claimed[0]?.allocation_seq).toBe(1);
    await releaseHighest({ db, competitionId, allocationId: 'a1', count: 1, now: NOW, reason: 'REFUND', returnToPool: true });
    const second = await claim('a2', 1);
    // Same number, second issue -- internally unambiguous.
    expect(second.ok && second.claimed[0]?.entry_number).toBe('PUT1001');
    expect(second.ok && second.claimed[0]?.allocation_seq).toBe(2);
  });
});

describe('release is LIFO — highest first', () => {
  test('a partial release gives back the highest numbers and keeps the low block', async () => {
    await claim('a1', 4); // PUT1001..PUT1004
    const released = await releaseHighest({ db, competitionId, allocationId: 'a1', count: 2, now: NOW, reason: 'REFUND', returnToPool: true });
    expect(released.map((r) => r.entry_number)).toEqual(['PUT1004', 'PUT1003']);
    const stillHeld = await listHeld(db, competitionId, 'a1');
    expect(stillHeld.map((h) => h.entry_number)).toEqual(['PUT1001', 'PUT1002']);
  });

  test('a second partial release behaves identically', async () => {
    await claim('a1', 4);
    await releaseHighest({ db, competitionId, allocationId: 'a1', count: 1, now: NOW, reason: 'REFUND', returnToPool: true });
    await releaseHighest({ db, competitionId, allocationId: 'a1', count: 1, now: NOW, reason: 'REFUND', returnToPool: true });
    expect((await listHeld(db, competitionId, 'a1')).map((h) => h.entry_number)).toEqual(['PUT1001', 'PUT1002']);
  });

  test('releasing with returnToPool false leaves the number RELEASED and unavailable', async () => {
    await claim('a1', 2);
    await releaseHighest({ db, competitionId, allocationId: 'a1', count: 2, now: NOW, reason: 'CANCELLED', returnToPool: false });
    const rows = db.query<{ status: string }>(`SELECT status FROM entry_number WHERE seq IN (1001,1002)`);
    expect(rows.every((r) => r.status === 'RELEASED')).toBe(true);
    // They are NOT reissued.
    const next = await claim('a2', 2);
    expect(next.ok && next.claimed.map((c) => c.entry_number)).toEqual(['PUT1003', 'PUT1004']);
  });

  test('a release records its reason', async () => {
    await claim('a1', 1);
    await releaseHighest({ db, competitionId, allocationId: 'a1', count: 1, now: NOW, reason: 'CANCELLED', returnToPool: true });
    expect(db.query<{ release_reason: string }>(`SELECT release_reason FROM entry_number WHERE seq=1001`)[0]).toMatchObject({
      release_reason: 'CANCELLED',
    });
  });

  test('releasing more than held releases only what is held', async () => {
    await claim('a1', 2);
    const released = await releaseHighest({ db, competitionId, allocationId: 'a1', count: 10, now: NOW, reason: 'REFUND', returnToPool: true });
    expect(released).toHaveLength(2);
  });
});

describe('idempotency at the pool layer', () => {
  test('re-running the same claim would double-allocate, so callers must check held first', async () => {
    // This documents WHY process.ts checks held before claiming: claimLowest
    // itself is a primitive and will happily take more numbers.
    await claim('a1', 2);
    expect(await countHeld(db, competitionId, 'a1')).toBe(2);
    await claim('a1', 2);
    expect(await countHeld(db, competitionId, 'a1')).toBe(4);
  });

  test('countHeld is the idempotency signal and survives a missing ledger row', async () => {
    // No allocation row exists at all -- the pool still knows who holds what.
    await claim('orphan', 2);
    expect(await countHeld(db, competitionId, 'orphan')).toBe(2);
  });
});
