/**
 * End-to-end order processing against a mocked Shopify.
 *
 * No network, no real order, no payment. The order shapes below are modelled
 * on real orders observed on the live store (#1003 cancelled-but-PAID, #1004
 * quantity 2, #1006 GBP 0.00 PAID with no transaction).
 */
import { beforeEach, describe, expect, test } from 'vitest';
import { countHeld, listHeld } from '../src/allocate';
import { buildPool } from '../src/pool';
import { processOrder, toMinorUnits } from '../src/process';
import { ShopifyClient } from '../src/shopify';
import { allocationId } from '../src/idempotency';
import { NOW, TestD1, seedCompetition, silentLogger } from './helpers';

const SHOP = 'test.myshopify.com';
const PRODUCT_GID = 'gid://shopify/Product/900001';

let db: TestD1;
let competitionId: string;

interface OrderOpts {
  financialStatus?: string;
  cancelledAt?: string | null;
  test?: boolean;
  quantity?: number;
  currentQuantity?: number;
  skillAnswer?: string | null;
  correctAnswer?: string | null;
  variantEntries?: string | null;
}

function mockFetch(opts: OrderOpts) {
  return async (_url: string, init?: RequestInit): Promise<Response> => {
    const body = JSON.parse(String(init?.body ?? '{}')) as { query: string };
    if (body.query.includes('AllocatorOrder')) {
      return Response.json({
        data: {
          order: {
            id: 'gid://shopify/Order/1042',
            name: '#1042',
            createdAt: NOW,
            test: opts.test ?? false,
            cancelledAt: opts.cancelledAt ?? null,
            displayFinancialStatus: opts.financialStatus ?? 'PAID',
            customer: { id: 'gid://shopify/Customer/7' },
            lineItems: {
              nodes: [
                {
                  id: 'gid://shopify/LineItem/55',
                  quantity: opts.quantity ?? 3,
                  currentQuantity: opts.currentQuantity ?? opts.quantity ?? 3,
                  variant: { id: 'gid://shopify/ProductVariant/88' },
                  product: { id: PRODUCT_GID },
                  discountedUnitPriceAfterAllDiscountsSet: { shopMoney: { amount: '2.49' } },
                  discountedTotalSet: { shopMoney: { amount: '7.47' } },
                  customAttributes: [
                    { key: 'Skill answer', value: opts.skillAnswer ?? 'Bunker' },
                    { key: '_skill_question', value: 'What is a sand pit traditionally called in golf?' },
                    { key: '_entry_route', value: 'online' },
                  ],
                },
              ],
            },
          },
        },
      });
    }
    return Response.json({
      data: {
        product: {
          id: PRODUCT_GID,
          handle: 'win-a-putter',
          title: 'Win a TaylorMade Putter',
          status: 'ACTIVE',
          entriesTotal: { value: '20' },
          entryPrefix: { value: 'PUT' },
          entryStartNumber: { value: '1001' },
          skillQuestion: { value: 'What is a sand pit traditionally called in golf?' },
          skillAnswers: { value: JSON.stringify(['Rough', 'Bunker', 'Fairway']) },
          skillAnswerCorrect: opts.correctAnswer === undefined ? { value: 'Bunker' } : opts.correctAnswer === null ? null : { value: opts.correctAnswer },
          variants: { nodes: [{ id: 'gid://shopify/ProductVariant/88', entries: opts.variantEntries ? { value: opts.variantEntries } : null }] },
        },
      },
    });
  };
}

function deps(opts: OrderOpts) {
  return {
    db,
    shopify: new ShopifyClient({ store: SHOP, accessToken: 't', logger: silentLogger(), fetchImpl: mockFetch(opts) as never, backoffBaseMs: 0 }),
    logger: silentLogger(),
    shopDomain: SHOP,
    runId: 'run-1',
    actor: 'system:webhook' as const,
    now: () => NOW,
    webhookId: 'wh-1',
  };
}

const ORDER = 'gid://shopify/Order/1042';

beforeEach(async () => {
  db = new TestD1();
  competitionId = seedCompetition(db, { capacity: 20 }).competitionId;
  await buildPool(db, competitionId, { prefix: 'PUT', startNumber: 1001, capacity: 20, padWidth: 4 });
});

describe('a paid order', () => {
  test('receives the lowest numbers and writes a ledger row', async () => {
    const out = await processOrder(deps({}), ORDER, 'ORDER_EDIT');
    expect(out[0]?.claimed).toEqual(['PUT1001', 'PUT1002', 'PUT1003']);

    const key = await allocationId(SHOP, '1042', '55');
    const alloc = db.query<Record<string, unknown>>(`SELECT * FROM allocation WHERE allocation_id=?`, key)[0];
    expect(alloc).toMatchObject({
      order_name: '#1042',
      ordered_quantity: 3,
      entries_per_unit: 1,
      target_count: 3,
      held_count: 3,
      skill_verdict: 'CORRECT',
      skill_rule_version: 'v1',
      entry_route: 'online',
      status: 'ALLOCATED',
    });
  });

  test('snapshots the correct answer at allocation time', async () => {
    await processOrder(deps({}), ORDER, 'ORDER_EDIT');
    const key = await allocationId(SHOP, '1042', '55');
    expect(db.query<Record<string, unknown>>(`SELECT * FROM allocation WHERE allocation_id=?`, key)[0]).toMatchObject({
      skill_answer_correct_snapshot: 'Bunker',
      skill_answer: 'Bunker',
    });
  });

  test('uses the code-discount-aware money figures', async () => {
    await processOrder(deps({}), ORDER, 'ORDER_EDIT');
    const key = await allocationId(SHOP, '1042', '55');
    expect(db.query<Record<string, unknown>>(`SELECT * FROM allocation WHERE allocation_id=?`, key)[0]).toMatchObject({
      unit_price_minor: 249,
      line_total_minor: 747,
    });
  });

  test('writes an ALLOCATED event per number', async () => {
    await processOrder(deps({}), ORDER, 'ORDER_EDIT');
    const events = db.query<{ entry_number: string }>(
      `SELECT entry_number FROM entry_event WHERE event_type='ALLOCATED' ORDER BY seq`,
    );
    expect(events.map((e) => e.entry_number)).toEqual(['PUT1001', 'PUT1002', 'PUT1003']);
  });

  test('bundle mode multiplies by the variant entries metafield', async () => {
    const out = await processOrder(deps({ quantity: 2, variantEntries: '5' }), ORDER, 'ORDER_EDIT');
    expect(out[0]?.claimed).toHaveLength(10);
  });
});

describe('idempotency', () => {
  test('processing the same order twice allocates once', async () => {
    await processOrder(deps({}), ORDER, 'ORDER_EDIT');
    const second = await processOrder(deps({}), ORDER, 'ORDER_EDIT');
    expect(second[0]?.action).toBe('NONE');
    expect(second[0]?.claimed).toEqual([]);

    const key = await allocationId(SHOP, '1042', '55');
    expect(await countHeld(db, competitionId, key)).toBe(3);
    expect(db.query(`SELECT * FROM allocation`)).toHaveLength(1);
  });

  test('processing five times still allocates once', async () => {
    for (let i = 0; i < 5; i++) await processOrder(deps({}), ORDER, 'ORDER_EDIT');
    const key = await allocationId(SHOP, '1042', '55');
    expect(await countHeld(db, competitionId, key)).toBe(3);
    expect(db.query<{ n: number }>(`SELECT COUNT(*) AS n FROM entry_number WHERE status='ALLOCATED'`)[0]).toMatchObject({ n: 3 });
  });

  test('the same customer does NOT receive two sets of entries', async () => {
    await processOrder(deps({}), ORDER, 'ORDER_EDIT');
    await processOrder(deps({}), ORDER, 'ORDER_EDIT');
    const key = await allocationId(SHOP, '1042', '55');
    expect((await listHeld(db, competitionId, key)).map((h) => h.entry_number)).toEqual([
      'PUT1001', 'PUT1002', 'PUT1003',
    ]);
  });
});

describe('order states that do not allocate', () => {
  test.each(['PENDING', 'AUTHORIZED', 'PARTIALLY_PAID'])('%s holds and allocates nothing', async (status) => {
    const out = await processOrder(deps({ financialStatus: status }), ORDER, 'ORDER_EDIT');
    expect(out[0]?.action).toBe('HELD');
    expect(db.query<{ n: number }>(`SELECT COUNT(*) AS n FROM entry_number WHERE status='ALLOCATED'`)[0]).toMatchObject({ n: 0 });
    expect(db.query(`SELECT * FROM allocation`)).toHaveLength(0);
  });

  test('a test order allocates nothing', async () => {
    await processOrder(deps({ test: true }), ORDER, 'ORDER_EDIT');
    expect(db.query<{ n: number }>(`SELECT COUNT(*) AS n FROM entry_number WHERE status='ALLOCATED'`)[0]).toMatchObject({ n: 0 });
  });

  test.each(['VOIDED', 'EXPIRED'])('%s allocates nothing', async (status) => {
    await processOrder(deps({ financialStatus: status }), ORDER, 'ORDER_EDIT');
    expect(db.query<{ n: number }>(`SELECT COUNT(*) AS n FROM entry_number WHERE status='ALLOCATED'`)[0]).toMatchObject({ n: 0 });
  });

  test('a later PENDING->PAID transition then allocates', async () => {
    await processOrder(deps({ financialStatus: 'PENDING' }), ORDER, 'ORDER_EDIT');
    const out = await processOrder(deps({ financialStatus: 'PAID' }), ORDER, 'ORDER_EDIT');
    expect(out[0]?.claimed).toEqual(['PUT1001', 'PUT1002', 'PUT1003']);
  });
});

describe('refunds and cancellation end to end', () => {
  test('a cancelled order releases everything it held', async () => {
    await processOrder(deps({}), ORDER, 'ORDER_EDIT');
    const out = await processOrder(deps({ cancelledAt: '2026-09-23T11:00:00Z', currentQuantity: 0 }), ORDER, 'CANCELLED');
    expect(out[0]?.released).toEqual(['PUT1003', 'PUT1002', 'PUT1001']);
    const key = await allocationId(SHOP, '1042', '55');
    expect(await countHeld(db, competitionId, key)).toBe(0);
    expect(db.query<{ n: number }>(`SELECT COUNT(*) AS n FROM entry_number WHERE status='AVAILABLE'`)[0]).toMatchObject({ n: 20 });
  });

  test('a partial refund releases the highest numbers only', async () => {
    await processOrder(deps({ quantity: 5 }), ORDER, 'ORDER_EDIT');
    const out = await processOrder(deps({ quantity: 5, currentQuantity: 3 }), ORDER, 'REFUND');
    expect(out[0]?.released).toEqual(['PUT1005', 'PUT1004']);
    const key = await allocationId(SHOP, '1042', '55');
    expect((await listHeld(db, competitionId, key)).map((h) => h.entry_number)).toEqual(['PUT1001', 'PUT1002', 'PUT1003']);
  });

  test('a money-only refund releases nothing, because currentQuantity is unchanged', async () => {
    await processOrder(deps({ quantity: 3 }), ORDER, 'ORDER_EDIT');
    const out = await processOrder(deps({ quantity: 3, currentQuantity: 3, financialStatus: 'PARTIALLY_REFUNDED' }), ORDER, 'REFUND');
    expect(out[0]?.released).toEqual([]);
    const key = await allocationId(SHOP, '1042', '55');
    expect(await countHeld(db, competitionId, key)).toBe(3);
  });

  test('released numbers are reissued lowest-first to the next order', async () => {
    await processOrder(deps({}), ORDER, 'ORDER_EDIT');
    await processOrder(deps({ cancelledAt: '2026-09-23T11:00:00Z', currentQuantity: 0 }), ORDER, 'CANCELLED');
    // A different order now claims: it must receive PUT1001 again.
    const { claimLowest } = await import('../src/allocate');
    const next = await claimLowest({ db, competitionId, allocationId: 'next', count: 2, now: NOW, orderId: 'o2', lineItemId: 'l2', customerRef: null });
    expect(next.ok && next.claimed.map((c) => c.entry_number)).toEqual(['PUT1001', 'PUT1002']);
  });

  test('the full history of a reissued number survives', async () => {
    await processOrder(deps({}), ORDER, 'ORDER_EDIT');
    await processOrder(deps({ cancelledAt: '2026-09-23T11:00:00Z', currentQuantity: 0 }), ORDER, 'CANCELLED');
    const history = db.query<{ event_type: string }>(
      `SELECT event_type FROM entry_event WHERE competition_id=? AND seq=1001 ORDER BY id`,
      competitionId,
    );
    expect(history.map((h) => h.event_type)).toEqual(['ALLOCATED', 'RELEASED', 'RETURNED_TO_POOL']);
  });
});

describe('skill verdicts', () => {
  test('a wrong answer is allocated but marked INCORRECT — numbers are NOT voided', async () => {
    const out = await processOrder(deps({ skillAnswer: 'Fairway' }), ORDER, 'ORDER_EDIT');
    expect(out[0]?.claimed).toHaveLength(3);
    const key = await allocationId(SHOP, '1042', '55');
    expect(db.query<Record<string, unknown>>(`SELECT * FROM allocation WHERE allocation_id=?`, key)[0]).toMatchObject({
      skill_verdict: 'INCORRECT',
    });
    expect(await countHeld(db, competitionId, key)).toBe(3);
    expect(db.query(`SELECT * FROM entry_event WHERE event_type='INCORRECT_SKILL'`)).toHaveLength(1);
  });

  test('a missing correct answer yields UNJUDGED and records the blocker', async () => {
    const out = await processOrder(deps({ correctAnswer: null }), ORDER, 'ORDER_EDIT');
    expect(out[0]?.claimed).toHaveLength(3);
    const key = await allocationId(SHOP, '1042', '55');
    expect(db.query<Record<string, unknown>>(`SELECT * FROM allocation WHERE allocation_id=?`, key)[0]).toMatchObject({
      skill_verdict: 'UNJUDGED',
      skill_rule_version: null,
    });
    expect(db.query(`SELECT * FROM entry_event WHERE event_type='UNJUDGED_SKILL'`)).toHaveLength(1);
  });
});

describe('unknown products', () => {
  test('a line whose product is not a registered competition is skipped', async () => {
    const fresh = new TestD1(); // no competition seeded
    const out = await processOrder({ ...deps({}), db: fresh }, ORDER, 'ORDER_EDIT');
    expect(out[0]?.action).toBe('SKIPPED');
  });
});

describe('money parsing', () => {
  test('converts to pence without float drift', () => {
    expect(toMinorUnits('2.49')).toBe(249);
    expect(toMinorUnits('0.00')).toBe(0);
    expect(toMinorUnits('0')).toBe(0);
    expect(toMinorUnits('10')).toBe(1000);
    expect(toMinorUnits('4.9')).toBe(490);
    expect(toMinorUnits('1234.56')).toBe(123456);
  });
});
