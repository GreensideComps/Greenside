/**
 * Concurrency.
 *
 * SQLite serialises writes within a connection and D1 serialises writes to a
 * database, so these tests exercise the property that actually matters: that
 * CLAIM_LOWEST is a SINGLE statement whose capacity guard and row selection
 * are evaluated together. No interleaving is possible inside it, so no two
 * claimants can be handed the same number and no claim can half-succeed.
 */
import { beforeEach, describe, expect, test } from 'vitest';
import { claimLowest, countHeld } from '../src/allocate';
import { buildPool } from '../src/pool';
import { NOW, TestD1, seedCompetition } from './helpers';

let db: TestD1;
let competitionId: string;

beforeEach(async () => {
  db = new TestD1();
  competitionId = seedCompetition(db, { capacity: 20 }).competitionId;
  await buildPool(db, competitionId, { prefix: 'PUT', startNumber: 1001, capacity: 20, padWidth: 4 });
});

const claimFor = (allocationId: string, count: number) =>
  claimLowest({ db, competitionId, allocationId, count, now: NOW, orderId: `o-${allocationId}`, lineItemId: 'l', customerRef: null });

describe('simultaneous claims receive disjoint numbers', () => {
  test('ten concurrent claims of 2 share no number', async () => {
    const results = await Promise.all(Array.from({ length: 10 }, (_, i) => claimFor(`a${i}`, 2)));
    const all: string[] = [];
    for (const r of results) {
      expect(r.ok).toBe(true);
      if (r.ok) all.push(...r.claimed.map((c) => c.entry_number));
    }
    expect(all).toHaveLength(20);
    expect(new Set(all).size).toBe(20); // no duplicates anywhere
  });

  test('the whole pool is consumed exactly once, with no gaps and no repeats', async () => {
    await Promise.all(Array.from({ length: 10 }, (_, i) => claimFor(`a${i}`, 2)));
    const rows = db.query<{ entry_number: string; status: string }>(
      `SELECT entry_number, status FROM entry_number WHERE competition_id=? ORDER BY seq`,
      competitionId,
    );
    expect(rows).toHaveLength(20);
    expect(rows.every((r) => r.status === 'ALLOCATED')).toBe(true);
    expect(new Set(rows.map((r) => r.entry_number)).size).toBe(20);
  });

  test('each claimant gets a contiguous block', async () => {
    const results = await Promise.all(Array.from({ length: 5 }, (_, i) => claimFor(`b${i}`, 4)));
    for (const r of results) {
      expect(r.ok).toBe(true);
      if (!r.ok) continue;
      const seqs = r.claimed.map((c) => c.seq);
      expect(seqs).toEqual([...seqs].sort((x, y) => x - y));
      expect(seqs[seqs.length - 1]! - seqs[0]!).toBe(seqs.length - 1);
    }
  });
});

describe('over-subscription under concurrency', () => {
  test('when demand exceeds the pool, some claims are refused and NONE is partial', async () => {
    // 20 numbers, 15 claimants wanting 2 each = demand for 30.
    const results = await Promise.all(Array.from({ length: 15 }, (_, i) => claimFor(`c${i}`, 2)));
    const granted = results.filter((r) => r.ok);
    const refused = results.filter((r) => !r.ok);

    expect(granted).toHaveLength(10);
    expect(refused).toHaveLength(5);

    // Every granted claim got EXACTLY what it asked for. All-or-nothing.
    for (const r of granted) if (r.ok) expect(r.claimed).toHaveLength(2);

    const allocated = db.query<{ n: number }>(
      `SELECT COUNT(*) AS n FROM entry_number WHERE competition_id=? AND status='ALLOCATED'`,
      competitionId,
    )[0];
    expect(allocated).toMatchObject({ n: 20 }); // never 21
  });

  test('a refused claimant holds nothing at all', async () => {
    const results = await Promise.all(Array.from({ length: 15 }, (_, i) => claimFor(`d${i}`, 2)));
    for (let i = 0; i < results.length; i++) {
      const r = results[i]!;
      if (!r.ok) expect(await countHeld(db, competitionId, `d${i}`)).toBe(0);
    }
  });

  test('an odd request size cannot straddle the capacity boundary', async () => {
    // 20 numbers, claims of 3: six succeed (18), the seventh wants 3 but only
    // 2 remain, so it must be refused rather than given 2.
    const results = await Promise.all(Array.from({ length: 7 }, (_, i) => claimFor(`e${i}`, 3)));
    const granted = results.filter((r) => r.ok);
    expect(granted).toHaveLength(6);
    for (const r of granted) if (r.ok) expect(r.claimed).toHaveLength(3);
    const remaining = db.query<{ n: number }>(
      `SELECT COUNT(*) AS n FROM entry_number WHERE competition_id=? AND status='AVAILABLE'`,
      competitionId,
    )[0];
    expect(remaining).toMatchObject({ n: 2 });
  });
});

describe('repeated runs are stable', () => {
  test('20 independent pools all consume cleanly', async () => {
    for (let run = 0; run < 20; run++) {
      const fresh = new TestD1();
      const id = seedCompetition(fresh, { capacity: 8 }).competitionId;
      await buildPool(fresh, id, { prefix: 'PUT', startNumber: 1, capacity: 8, padWidth: 2 });
      const results = await Promise.all(
        Array.from({ length: 4 }, (_, i) =>
          claimLowest({ db: fresh, competitionId: id, allocationId: `x${i}`, count: 2, now: NOW, orderId: 'o', lineItemId: 'l', customerRef: null }),
        ),
      );
      const all = results.flatMap((r) => (r.ok ? r.claimed.map((c) => c.entry_number) : []));
      expect(new Set(all).size).toBe(8);
    }
  });
});
