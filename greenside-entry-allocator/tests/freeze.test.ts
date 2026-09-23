import { beforeEach, describe, expect, test } from 'vitest';
import { claimLowest } from '../src/allocate';
import { freeze, freezeBlockers } from '../src/freeze';
import { buildPool } from '../src/pool';
import { readSnapshot } from '../src/snapshot';
import { NOW, TestD1, seedAllocation, seedCompetition } from './helpers';

let db: TestD1;
let competitionId: string;

async function setup(opts: { capacity?: number; status?: string } = {}) {
  db = new TestD1();
  const capacity = opts.capacity ?? 10;
  competitionId = seedCompetition(db, { capacity, status: opts.status ?? 'OPEN' }).competitionId;
  await buildPool(db, competitionId, { prefix: 'PUT', startNumber: 1001, capacity, padWidth: 4 });
}

beforeEach(async () => {
  await setup();
});

async function allocate(allocationId: string, count: number, verdict: 'CORRECT' | 'INCORRECT' | 'UNJUDGED' = 'CORRECT') {
  seedAllocation(db, { allocationId, competitionId, orderId: `o-${allocationId}`, lineItemId: `l-${allocationId}`, verdict });
  await claimLowest({ db, competitionId, allocationId, count, now: NOW, orderId: `o-${allocationId}`, lineItemId: `l-${allocationId}`, customerRef: 'c' });
}

const doFreeze = (capacity = 10) =>
  freeze({ db, competitionId, competitionStatus: 'OPEN', expectedCapacity: capacity, configValid: true, now: NOW });

describe('OPEN -> FROZEN', () => {
  test('a clean competition freezes and reports the eligible count', async () => {
    await allocate('a1', 3);
    const result = await doFreeze();
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.eligible).toBe(3);
    expect(db.query<{ status: string; frozen_at: string }>(`SELECT status, frozen_at FROM competition`)[0]).toMatchObject({
      status: 'FROZEN',
    });
  });

  test('freezing twice is refused the second time', async () => {
    await allocate('a1', 1);
    expect((await doFreeze()).ok).toBe(true);
    const second = await freeze({ db, competitionId, competitionStatus: 'FROZEN', expectedCapacity: 10, configValid: true, now: NOW });
    expect(second.ok).toBe(false);
    if (second.ok) return;
    expect(second.blockers.map((b) => b.code)).toContain('NOT_OPEN');
  });
});

describe('freeze refuses rather than warns', () => {
  test('an UNJUDGED allocation blocks the freeze', async () => {
    await allocate('a1', 2, 'UNJUDGED');
    const result = await doFreeze();
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.blockers.map((b) => b.code)).toContain('UNJUDGED_ALLOCATIONS');
    // Still OPEN.
    expect(db.query<{ status: string }>(`SELECT status FROM competition`)[0]).toMatchObject({ status: 'OPEN' });
  });

  test('an invalid configuration blocks the freeze', async () => {
    await allocate('a1', 1);
    const result = await freeze({
      db, competitionId, competitionStatus: 'OPEN', expectedCapacity: 10,
      configValid: false, configDetail: 'skill_answer_correct missing', now: NOW,
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.blockers.map((b) => b.code)).toContain('CONFIG_INVALID');
  });

  test('an orphaned claim blocks the freeze', async () => {
    // Numbers claimed by an allocation_id with no ledger row: a crash between
    // the claim and the ledger write.
    await claimLowest({ db, competitionId, allocationId: 'orphan', count: 2, now: NOW, orderId: 'o', lineItemId: 'l', customerRef: null });
    const result = await doFreeze();
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.blockers.map((b) => b.code)).toContain('ORPHANED_CLAIMS');
  });

  test('a pool smaller than the declared capacity blocks the freeze', async () => {
    await allocate('a1', 1);
    const result = await freeze({ db, competitionId, competitionStatus: 'OPEN', expectedCapacity: 999, configValid: true, now: NOW });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.blockers.map((b) => b.code)).toContain('POOL_SIZE_MISMATCH');
  });

  test('a non-OPEN competition cannot be frozen', async () => {
    await setup({ status: 'DRAFT' });
    const result = await freeze({ db, competitionId, competitionStatus: 'DRAFT', expectedCapacity: 10, configValid: true, now: NOW });
    expect(result.ok).toBe(false);
  });

  test('every blocker is reported together, not just the first', async () => {
    await allocate('a1', 1, 'UNJUDGED');
    await claimLowest({ db, competitionId, allocationId: 'orphan', count: 1, now: NOW, orderId: 'o', lineItemId: 'l', customerRef: null });
    const blockers = await freezeBlockers({
      db, competitionId, competitionStatus: 'OPEN', expectedCapacity: 999, configValid: false,
    });
    expect(blockers.length).toBeGreaterThanOrEqual(4);
  });

  test('freezeBlockers is read-only and leaves the status alone', async () => {
    await allocate('a1', 1, 'UNJUDGED');
    await freezeBlockers({ db, competitionId, competitionStatus: 'OPEN', expectedCapacity: 10, configValid: true });
    expect(db.query<{ status: string }>(`SELECT status FROM competition`)[0]).toMatchObject({ status: 'OPEN' });
  });
});

describe('no number reuse after freeze', () => {
  test('a number released after the freeze stays RELEASED and is never reissued', async () => {
    await allocate('a1', 2);
    await doFreeze();

    // A refund arrives after the freeze.
    const { converge } = await import('../src/converge');
    await converge({
      db, competitionId, competitionStatus: 'FROZEN', allocationId: 'a1',
      currentQuantity: 0, entriesPerUnit: 1, now: NOW, reason: 'REFUND',
      orderId: 'o-a1', lineItemId: 'l-a1', customerRef: null,
    });

    const released = db.query<{ status: string }>(`SELECT status FROM entry_number WHERE seq IN (1001,1002)`);
    expect(released.every((r) => r.status === 'RELEASED')).toBe(true);

    // Even a direct claim attempt cannot take them back: they are not AVAILABLE.
    const attempt = await claimLowest({ db, competitionId, allocationId: 'a2', count: 2, now: NOW, orderId: 'o2', lineItemId: 'l2', customerRef: null });
    expect(attempt.ok && attempt.claimed.map((c) => c.entry_number)).toEqual(['PUT1003', 'PUT1004']);
  });
});

describe('the frozen snapshot', () => {
  test('lists eligible entries ascending and excludes incorrect answers', async () => {
    await allocate('good', 2, 'CORRECT');
    await allocate('bad', 2, 'INCORRECT');
    await doFreeze();

    const snap = await readSnapshot({ db, competitionId, competitionStatus: 'FROZEN', frozenAt: NOW });
    expect(snap.entries.map((e) => e.entry_number)).toEqual(['PUT1001', 'PUT1002']);
    expect(snap.eligibleCount).toBe(2);
    // The wrong answer keeps its numbers -- they are excluded, not voided.
    expect(snap.exclusions.map((e) => e.entry_number)).toEqual(['PUT1003', 'PUT1004']);
    expect(db.query<{ status: string }>(`SELECT status FROM entry_number WHERE seq=1003`)[0]).toMatchObject({
      status: 'ALLOCATED',
    });
  });

  test('the snapshot carries ownership, so the draw never has to ask "who holds this now"', async () => {
    await allocate('a1', 1);
    await doFreeze();
    const snap = await readSnapshot({ db, competitionId, competitionStatus: 'FROZEN', frozenAt: NOW });
    expect(snap.entries[0]).toMatchObject({ entry_number: 'PUT1001', order_id: 'o-a1', skill_verdict: 'CORRECT' });
  });

  test('the snapshot module exposes no draw capability', async () => {
    const mod = await import('../src/snapshot');
    const names = Object.keys(mod).join(' ').toLowerCase();
    for (const forbidden of ['random', 'beacon', 'winner', 'draw', 'commit', 'hash', 'redraw', 'verify']) {
      expect(names).not.toContain(forbidden);
    }
  });
});
