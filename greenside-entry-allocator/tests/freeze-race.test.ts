/**
 * The freeze race: processLine reads the competition as OPEN, a freeze
 * commits, and only then does the convergence batch execute.
 *
 * The batch must honour the freeze it did not see: no claim, and a release
 * leaves the number RELEASED rather than returning it to the pool.
 *
 * Written against D1Like so the same scenarios can run on any backend.
 */
import { describe, expect, test } from 'vitest';
import type { D1Like, D1StatementLike } from '../src/db';
import { freeze, freezeBlockers } from '../src/freeze';
import { buildPool } from '../src/pool';
import { processOrder } from '../src/process';
import { NOW, TestD1 } from './helpers';
import { ORDER, SHOP, deps, type OrderOpts } from './order-fixtures';

export const BACKENDS: Record<string, () => Promise<D1Like>> = {
  'node:sqlite': async () => new TestD1(),
};

/** Commits a real freeze immediately before the first batch -- after every read processLine makes. */
class FreezeBeforeBatch implements D1Like {
  frozen: Awaited<ReturnType<typeof freeze>> | null = null;
  constructor(private readonly db: D1Like) {}
  prepare(sql: string): D1StatementLike {
    return this.db.prepare(sql);
  }
  async batch(statements: D1StatementLike[]): Promise<unknown[]> {
    if (!this.frozen) {
      this.frozen = await freeze({ db: this.db, competitionId: '900001', competitionStatus: 'OPEN', expectedCapacity: 10, configValid: true, now: NOW });
    }
    return this.db.batch(statements);
  }
  exec(sql: string): Promise<unknown> {
    return this.db.exec(sql);
  }
}

async function competition(db: D1Like): Promise<void> {
  await db
    .prepare(
      `INSERT INTO competition (competition_id, shop_domain, product_gid, prefix, start_number, capacity, pad_width,
         handle_snapshot, title_snapshot, skill_answer_correct_snapshot, status, created_at, updated_at, pool_built_at)
       VALUES ('900001', ?1, 'gid://shopify/Product/900001', 'PUT', 1001, 10, 4, 'h', 't', 'Bunker', 'OPEN', ?2, ?2, ?2)`,
    )
    .bind(SHOP, NOW)
    .run();
  await buildPool(db, '900001', { prefix: 'PUT', startNumber: 1001, capacity: 10, padWidth: 4 });
}

const rows = async <T>(db: D1Like, sql: string) => (await db.prepare(sql).all<T>()).results;
const pool = (db: D1Like) => rows<{ seq: number; status: string }>(db, `SELECT seq, status FROM entry_number ORDER BY seq`);
const eventTypes = async (db: D1Like) =>
  (await rows<{ event_type: string }>(db, `SELECT event_type FROM entry_event ORDER BY id`)).map((r) => r.event_type);
const auditGaps = async (db: D1Like) =>
  (await freezeBlockers({ db, competitionId: '900001', competitionStatus: 'FROZEN', expectedCapacity: 10, configValid: true }))
    .map((b) => b.code)
    .filter((c) => c !== 'NOT_OPEN');

let run = 0;
const processRacing = async (db: D1Like, o: OrderOpts, reason: 'ORDER_EDIT' | 'REFUND' | 'CANCELLED') => {
  const racing = new FreezeBeforeBatch(db);
  await processOrder(deps(racing, o, `race-${run++}`), ORDER, reason);
  expect(racing.frozen?.ok).toBe(true); // the freeze itself was clean, and committed mid-flight
  expect((await rows<{ status: string }>(db, `SELECT status FROM competition`))[0]!.status).toBe('FROZEN');
};

for (const [backend, make] of Object.entries(BACKENDS)) {
  describe(`a freeze committing between the status read and the batch (${backend})`, () => {
    test('a release stays RELEASED: nothing returns to the pool after the freeze', async () => {
      const db = await make();
      await competition(db);
      await processOrder(deps(db, { quantity: 3, currentQuantity: 3 }, `r${run++}`), ORDER, 'ORDER_EDIT');
      const before = await pool(db);

      await processRacing(db, { quantity: 3, currentQuantity: 1 }, 'REFUND');

      const after = await pool(db);
      expect(after.filter((r) => r.status === 'AVAILABLE')).toEqual(before.filter((r) => r.status === 'AVAILABLE'));
      expect(after.slice(0, 3)).toEqual([
        { seq: 1001, status: 'ALLOCATED' },
        { seq: 1002, status: 'RELEASED' },
        { seq: 1003, status: 'RELEASED' },
      ]);
      expect(await rows(db, `SELECT seq, to_status FROM entry_event WHERE event_type = 'RELEASED' ORDER BY seq`)).toEqual([
        { seq: 1002, to_status: 'RELEASED' },
        { seq: 1003, to_status: 'RELEASED' },
      ]);
      expect(await eventTypes(db)).not.toContain('RETURNED_TO_POOL');
      expect(await rows(db, `SELECT held_count, target_count FROM allocation`)).toEqual([{ held_count: 1, target_count: 1 }]);
      expect(await auditGaps(db)).toEqual([]);
    });

    test('a cancellation after the freeze releases everything, and none of it becomes available', async () => {
      const db = await make();
      await competition(db);
      await processOrder(deps(db, { quantity: 3, currentQuantity: 3 }, `r${run++}`), ORDER, 'ORDER_EDIT');

      await processRacing(db, { quantity: 3, currentQuantity: 0, cancelledAt: NOW }, 'CANCELLED');

      expect((await pool(db)).filter((r) => r.status === 'RELEASED').map((r) => r.seq)).toEqual([1001, 1002, 1003]);
      expect((await pool(db)).filter((r) => r.status === 'AVAILABLE')).toHaveLength(7);
      expect(await eventTypes(db)).toEqual(['ALLOCATED', 'ALLOCATED', 'ALLOCATED', 'RELEASED', 'RELEASED', 'RELEASED']);
      expect(await auditGaps(db)).toEqual([]);
    });

    test('a first allocation claims nothing, records no numbers and no capacity refusal', async () => {
      const db = await make();
      await competition(db);

      await processRacing(db, { quantity: 3, currentQuantity: 3 }, 'ORDER_EDIT');

      expect((await pool(db)).every((r) => r.status === 'AVAILABLE')).toBe(true);
      expect(await eventTypes(db)).toEqual([]);
      // The ledger row commits with the batch, holding nothing (existing model, unchanged here).
      expect(await rows(db, `SELECT held_count, target_count, status FROM allocation`)).toEqual([
        { held_count: 0, target_count: 3, status: 'RELEASED' },
      ]);
      expect(await auditGaps(db)).toEqual([]);
    });

    test('an increase for an existing allocation claims nothing and is not a capacity refusal', async () => {
      const db = await make();
      await competition(db);
      await processOrder(deps(db, { quantity: 3, currentQuantity: 3 }, `r${run++}`), ORDER, 'ORDER_EDIT');
      const before = await pool(db);

      await processRacing(db, { quantity: 5, currentQuantity: 5 }, 'ORDER_EDIT');

      expect(await pool(db)).toEqual(before);
      expect(await eventTypes(db)).toEqual(['ALLOCATED', 'ALLOCATED', 'ALLOCATED']);
      expect(await rows(db, `SELECT held_count, target_count, status FROM allocation`)).toEqual([
        { held_count: 3, target_count: 5, status: 'PARTIAL' },
      ]);
      expect(await auditGaps(db)).toEqual([]);
    });

    test('a manufactured gap after the race is still caught by the audit', async () => {
      const db = await make();
      await competition(db);
      await processOrder(deps(db, { quantity: 3, currentQuantity: 3 }, `r${run++}`), ORDER, 'ORDER_EDIT');
      await processRacing(db, { quantity: 3, currentQuantity: 1 }, 'REFUND');
      await db.prepare(`DELETE FROM entry_event WHERE event_type = 'RELEASED' AND seq = 1003`).run();
      expect(await auditGaps(db)).toContain('AUDIT_GAP');
    });
  });
}
