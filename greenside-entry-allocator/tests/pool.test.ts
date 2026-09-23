import { beforeEach, describe, expect, test } from 'vitest';
import { buildPool, poolCounts } from '../src/pool';
import type { NumberingConfig } from '../src/numbering';
import { NOW, TestD1, seedCompetition } from './helpers';

let db: TestD1;
const PUT: NumberingConfig = { prefix: 'PUT', startNumber: 1001, capacity: 100, padWidth: 4 };

beforeEach(() => {
  db = new TestD1();
});

describe('creation', () => {
  test('materialises exactly capacity rows, PUT1001..PUT1100, all AVAILABLE', async () => {
    const { competitionId } = seedCompetition(db);
    const result = await buildPool(db, competitionId, PUT);
    expect(result).toEqual({ ok: true, created: 100, alreadyPresent: 0 });

    const counts = await poolCounts(db, competitionId);
    expect(counts.total).toBe(100);
    expect(counts.available).toBe(100);

    const rows = db.query<{ entry_number: string; seq: number }>(
      `SELECT entry_number, seq FROM entry_number WHERE competition_id=? ORDER BY seq`,
      competitionId,
    );
    expect(rows[0]).toMatchObject({ entry_number: 'PUT1001', seq: 1001 });
    expect(rows[99]).toMatchObject({ entry_number: 'PUT1100', seq: 1100 });
  });

  test('a different competition gets its own prefix and range', async () => {
    const a = seedCompetition(db, { competition_id: '1', prefix: 'PUT', start_number: 1001, capacity: 3 });
    const b = seedCompetition(db, { competition_id: '2', prefix: 'DRV', start_number: 5001, capacity: 3 });
    await buildPool(db, a.competitionId, { prefix: 'PUT', startNumber: 1001, capacity: 3, padWidth: 4 });
    await buildPool(db, b.competitionId, { prefix: 'DRV', startNumber: 5001, capacity: 3, padWidth: 4 });
    expect(db.query(`SELECT entry_number FROM entry_number ORDER BY entry_number`).map((r: any) => r.entry_number)).toEqual([
      'DRV5001', 'DRV5002', 'DRV5003', 'PUT1001', 'PUT1002', 'PUT1003',
    ]);
  });
});

describe('idempotent creation', () => {
  test('building twice creates nothing the second time', async () => {
    const { competitionId } = seedCompetition(db);
    await buildPool(db, competitionId, PUT);
    const second = await buildPool(db, competitionId, PUT);
    expect(second).toEqual({ ok: true, created: 0, alreadyPresent: 100 });
    expect((await poolCounts(db, competitionId)).total).toBe(100);
  });

  test('rebuilding does not disturb an allocated number', async () => {
    const { competitionId } = seedCompetition(db);
    await buildPool(db, competitionId, PUT);
    db.sqlite
      .prepare(`UPDATE entry_number SET status='ALLOCATED', allocation_id='a1', allocated_at=? WHERE seq=1001`)
      .run(NOW);
    await buildPool(db, competitionId, PUT);
    const row = db.query<{ status: string; allocation_id: string }>(
      `SELECT status, allocation_id FROM entry_number WHERE seq=1001`,
    )[0];
    expect(row).toMatchObject({ status: 'ALLOCATED', allocation_id: 'a1' });
  });
});

describe('growth', () => {
  test('raising capacity appends the new numbers only', async () => {
    const { competitionId } = seedCompetition(db, { capacity: 10 });
    await buildPool(db, competitionId, { ...PUT, capacity: 10 });
    const grown = await buildPool(db, competitionId, { ...PUT, capacity: 15 });
    expect(grown).toEqual({ ok: true, created: 5, alreadyPresent: 10 });
    expect((await poolCounts(db, competitionId)).total).toBe(15);
    expect(db.query(`SELECT entry_number FROM entry_number ORDER BY seq DESC LIMIT 1`)[0]).toMatchObject({
      entry_number: 'PUT1015',
    });
  });

  test('growth never renumbers an existing entry', async () => {
    const { competitionId } = seedCompetition(db, { capacity: 3 });
    await buildPool(db, competitionId, { ...PUT, capacity: 3 });
    const before = db.query(`SELECT entry_number, seq FROM entry_number ORDER BY seq`);
    await buildPool(db, competitionId, { ...PUT, capacity: 6 });
    const after = db.query(`SELECT entry_number, seq FROM entry_number ORDER BY seq LIMIT 3`);
    expect(after).toEqual(before);
  });
});

describe('shrink refusal', () => {
  test('shrinking is refused when a number that would disappear is allocated', async () => {
    const { competitionId } = seedCompetition(db, { capacity: 10 });
    await buildPool(db, competitionId, { ...PUT, capacity: 10 });
    db.sqlite
      .prepare(`UPDATE entry_number SET status='ALLOCATED', allocation_id='a1', allocated_at=? WHERE seq=1009`)
      .run(NOW);

    const shrunk = await buildPool(db, competitionId, { ...PUT, capacity: 5 });
    expect(shrunk.ok).toBe(false);
    if (shrunk.ok) return;
    expect(shrunk.code).toBe('SHRINK_REFUSED');
    // Nothing was removed.
    expect((await poolCounts(db, competitionId)).total).toBe(10);
  });

  test('shrinking is refused when a number that would disappear is RELEASED', async () => {
    const { competitionId } = seedCompetition(db, { capacity: 10 });
    await buildPool(db, competitionId, { ...PUT, capacity: 10 });
    db.sqlite.prepare(`UPDATE entry_number SET status='RELEASED', released_at=? WHERE seq=1010`).run(NOW);
    const shrunk = await buildPool(db, competitionId, { ...PUT, capacity: 5 });
    expect(shrunk.ok).toBe(false);
  });

  test('a shrink whose removed numbers are all free does not delete anything either', async () => {
    // Append-only: the pool never contracts. A smaller capacity is simply a
    // no-op rather than a destructive operation.
    const { competitionId } = seedCompetition(db, { capacity: 10 });
    await buildPool(db, competitionId, { ...PUT, capacity: 10 });
    const result = await buildPool(db, competitionId, { ...PUT, capacity: 5 });
    expect(result).toEqual({ ok: true, created: 0, alreadyPresent: 10 });
    expect((await poolCounts(db, competitionId)).total).toBe(10);
  });
});

describe('collision refusal', () => {
  test('a second competition whose range renders identical strings is refused', async () => {
    // Two competitions, same prefix in the pool (the competition table's
    // unique index is bypassed here deliberately to prove the SECOND,
    // independent guard on the rendered string).
    seedCompetition(db, { competition_id: '1', prefix: 'PUT' });
    seedCompetition(db, { competition_id: '2', prefix: 'DRV' });
    await buildPool(db, '1', { prefix: 'PUT', startNumber: 1001, capacity: 5, padWidth: 4 });
    const clash = await buildPool(db, '2', { prefix: 'PUT', startNumber: 1003, capacity: 5, padWidth: 4 });
    expect(clash.ok).toBe(false);
    if (clash.ok) return;
    expect(clash.code).toBe('DUPLICATE_ENTRY_NUMBER');
  });

  test('a refused collision leaves no partial pool behind', async () => {
    seedCompetition(db, { competition_id: '1', prefix: 'PUT' });
    seedCompetition(db, { competition_id: '2', prefix: 'DRV' });
    await buildPool(db, '1', { prefix: 'PUT', startNumber: 1001, capacity: 5, padWidth: 4 });
    await buildPool(db, '2', { prefix: 'PUT', startNumber: 1003, capacity: 5, padWidth: 4 });
    // The batch is a transaction, so the whole attempt rolled back.
    expect((await poolCounts(db, '2')).total).toBe(0);
  });
});
