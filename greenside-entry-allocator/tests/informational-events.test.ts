/**
 * REFUSED_CAPACITY and ORDER_HELD are written when the condition changes, not
 * every time a redelivery or reconciler pass observes it again.
 *
 * Written against D1Like so the same scenarios can run on any backend.
 */
import { describe, expect, test } from 'vitest';
import type { D1Like } from '../src/db';
import { freezeBlockers } from '../src/freeze';
import { buildPool } from '../src/pool';
import { processOrder } from '../src/process';
import { NOW, TestD1 } from './helpers';
import { ORDER, SHOP, deps, type OrderOpts } from './order-fixtures';

export const BACKENDS: Record<string, () => Promise<D1Like>> = {
  'node:sqlite': async () => new TestD1(),
};

async function competition(db: D1Like, capacity: number): Promise<void> {
  await db
    .prepare(
      `INSERT INTO competition (competition_id, shop_domain, product_gid, prefix, start_number, capacity, pad_width,
         handle_snapshot, title_snapshot, skill_answer_correct_snapshot, status, created_at, updated_at, pool_built_at)
       VALUES ('900001', ?1, 'gid://shopify/Product/900001', 'PUT', 1001, ?2, 4, 'h', 't', 'Bunker', 'OPEN', ?3, ?3, ?3)`,
    )
    .bind(SHOP, capacity, NOW)
    .run();
  await buildPool(db, '900001', { prefix: 'PUT', startNumber: 1001, capacity, padWidth: 4 });
}

const rows = async <T>(db: D1Like, sql: string) => (await db.prepare(sql).all<T>()).results;
const count = async (db: D1Like, type: string) =>
  (await rows<{ n: number }>(db, `SELECT COUNT(*) AS n FROM entry_event WHERE event_type = '${type}'`))[0]!.n;
const state = async (db: D1Like) =>
  JSON.stringify([
    await rows(db, `SELECT seq, status, allocation_id, allocation_seq FROM entry_number ORDER BY seq`),
    await rows(db, `SELECT allocation_id, target_count, held_count, status FROM allocation ORDER BY allocation_id`),
  ]);

let run = 0;
const deliver = (db: D1Like, o: OrderOpts, times = 1) =>
  (async () => {
    for (let i = 0; i < times; i++) await processOrder(deps(db, o, `run-${run++}`), ORDER, 'ORDER_EDIT');
  })();

for (const [backend, make] of Object.entries(BACKENDS)) {
  describe(`informational events are condition-change based (${backend})`, () => {
    test('a capacity refusal delivered 5 times records exactly one REFUSED_CAPACITY', async () => {
      const once = await make();
      const five = await make();
      await competition(once, 4);
      await competition(five, 4);
      await deliver(once, { quantity: 5, currentQuantity: 5 });
      await deliver(five, { quantity: 5, currentQuantity: 5 }, 5);
      expect(await count(five, 'REFUSED_CAPACITY')).toBe(1);
      expect(await state(five)).toBe(await state(once));
      const audit = await freezeBlockers({ db: five, competitionId: '900001', competitionStatus: 'OPEN', expectedCapacity: 4, configValid: true });
      expect(audit).toEqual([]);
    });

    test('ORDER_HELD delivered 5 times records exactly one ORDER_HELD', async () => {
      const db = await make();
      await competition(db, 20);
      await deliver(db, { quantity: 3, currentQuantity: 3, financialStatus: 'PENDING' }, 5);
      expect(await count(db, 'ORDER_HELD')).toBe(1);
      expect(await rows(db, `SELECT * FROM allocation`)).toEqual([]);
      expect(await rows(db, `SELECT * FROM entry_number WHERE status <> 'AVAILABLE'`)).toEqual([]);
    });

    test('an eligibility change (PENDING -> AUTHORIZED) records exactly one more ORDER_HELD', async () => {
      const db = await make();
      await competition(db, 20);
      await deliver(db, { quantity: 3, currentQuantity: 3, financialStatus: 'PENDING' }, 3);
      await deliver(db, { quantity: 3, currentQuantity: 3, financialStatus: 'AUTHORIZED' }, 3);
      expect(
        (await rows<{ detail_json: string }>(db, `SELECT detail_json FROM entry_event WHERE event_type = 'ORDER_HELD' ORDER BY id`)).map(
          (r) => JSON.parse(r.detail_json).code,
        ),
      ).toEqual(['HOLD_PENDING', 'HOLD_AUTHORIZED']);
      // Changing back is a change too.
      await deliver(db, { quantity: 3, currentQuantity: 3, financialStatus: 'PENDING' }, 3);
      expect(await count(db, 'ORDER_HELD')).toBe(3);
    });

    test('a materially different refusal (new target) records exactly one more REFUSED_CAPACITY', async () => {
      const db = await make();
      await competition(db, 4);
      await deliver(db, { quantity: 5, currentQuantity: 5 }, 3);
      await deliver(db, { quantity: 6, currentQuantity: 6 }, 3);
      expect(
        (await rows<{ detail_json: string }>(db, `SELECT detail_json FROM entry_event WHERE event_type = 'REFUSED_CAPACITY' ORDER BY id`)).map(
          (r) => JSON.parse(r.detail_json).target,
        ),
      ).toEqual([5, 6]);
    });

    test('a refusal after a successful claim is recorded once, and repeats add nothing', async () => {
      const db = await make();
      await competition(db, 4);
      await deliver(db, { quantity: 3, currentQuantity: 3 }); // claims PUT1001-1003
      expect(await count(db, 'ALLOCATED')).toBe(3);

      await deliver(db, { quantity: 5, currentQuantity: 5 }); // wants 2 more, 1 free: refused
      expect(await count(db, 'REFUSED_CAPACITY')).toBe(1);
      await deliver(db, { quantity: 5, currentQuantity: 5 }, 5);
      expect(await count(db, 'REFUSED_CAPACITY')).toBe(1);

      // A release in between is a new condition, so the next refusal is new.
      await processOrder(deps(db, { quantity: 5, currentQuantity: 2 }, `run-${run++}`), ORDER, 'REFUND');
      await deliver(db, { quantity: 6, currentQuantity: 6 }, 3); // wants 4 more, 2 free: refused
      expect(await count(db, 'REFUSED_CAPACITY')).toBe(2);
      expect(
        (await rows<{ event_type: string }>(db, `SELECT event_type FROM entry_event ORDER BY id`)).map((r) => r.event_type),
      ).toEqual(['ALLOCATED', 'ALLOCATED', 'ALLOCATED', 'REFUSED_CAPACITY', 'RELEASED', 'RETURNED_TO_POOL', 'REFUSED_CAPACITY']);
    });

    test('state-changing events keep their own idempotency: paid and refund replays write nothing new', async () => {
      const db = await make();
      await competition(db, 20);
      await deliver(db, { quantity: 3, currentQuantity: 3, answer: 'Rough' }, 5);
      for (let i = 0; i < 5; i++) await processOrder(deps(db, { quantity: 3, currentQuantity: 1, answer: 'Rough' }, `run-${run++}`), ORDER, 'REFUND');
      expect(
        Object.fromEntries(
          (await rows<{ event_type: string; n: number }>(db, `SELECT event_type, COUNT(*) AS n FROM entry_event GROUP BY event_type`)).map((r) => [r.event_type, r.n]),
        ),
      ).toEqual({ ALLOCATED: 3, INCORRECT_SKILL: 1, RELEASED: 2, RETURNED_TO_POOL: 2 });
    });
  });
}
