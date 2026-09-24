/**
 * U2 regression: the partial refund, pinned to a real observed order.
 *
 * Every number in ORDER_1009 below was read back from the live store after a
 * manual partial refund, not invented. Order #1009 was GBP 0.00 and PAID (a
 * 100%-discounted QA order), quantity 5, refunded by 2 units with NO_RESTOCK.
 *
 * The convergence rule rests on ONE assumption -- that currentQuantity, not
 * quantity, is the entitlement signal -- and this file is the thing that
 * fails if that assumption ever stops holding.
 *
 * Three traps this order exposed, all asserted below:
 *
 *   1. `quantity` did NOT move. It stayed at 5. Anything reading it as an
 *      entitlement would have released nothing at all.
 *   2. The refund created NO financial transactions and refunded GBP 0.00.
 *      A release detected from money movement would never have fired.
 *   3. The order stayed PAID, uncancelled and open. Eligibility still says
 *      "allocate"; convergence alone does the down-adjustment.
 */
import { beforeEach, describe, expect, test } from 'vitest';
import { countHeld, listHeld } from '../src/allocate';
import { targetCount } from '../src/converge';
import { allocationId } from '../src/idempotency';
import { buildPool } from '../src/pool';
import { processOrder } from '../src/process';
import { ShopifyClient } from '../src/shopify';
import { NOW, TestD1, seedCompetition, silentLogger } from './helpers';

/** The observed fixture. Read-only evidence; do not "tidy" these figures. */
const ORDER_1009 = {
  name: '#1009',
  displayFinancialStatus: 'PAID',
  cancelledAt: null,
  /** Immutable original. Unchanged by the refund. */
  quantity: 5,
  refundedUnits: 2,
  /** Post-refund. The entitlement signal. */
  currentQuantity: 3,
  /** Post-refund. Tracks currentQuantity. */
  refundableQuantity: 3,
  entriesPerUnit: 1,
  /** The refund moved no money and created no transaction. */
  refundTransactions: [] as unknown[],
  totalRefunded: '0.00',
} as const;

const SHOP = 'test.myshopify.com';
const PRODUCT_GID = 'gid://shopify/Product/900001';
const ORDER_GID = 'gid://shopify/Order/13549712998774';
const LINE_GID = 'gid://shopify/LineItem/39027315573110';

let db: TestD1;
let competitionId: string;

/** Mocked Shopify. No network, no real order, no payment, no refund. */
function mockFetch(currentQuantity: number) {
  return async (_url: string, init?: RequestInit): Promise<Response> => {
    const body = JSON.parse(String(init?.body ?? '{}')) as { query: string };
    if (body.query.includes('AllocatorOrder')) {
      return Response.json({
        data: {
          order: {
            id: ORDER_GID,
            name: ORDER_1009.name,
            createdAt: NOW,
            test: false,
            cancelledAt: ORDER_1009.cancelledAt,
            displayFinancialStatus: ORDER_1009.displayFinancialStatus,
            lineItems: {
              nodes: [
                {
                  id: LINE_GID,
                  // Both figures are carried, exactly as the API returns them.
                  quantity: ORDER_1009.quantity,
                  currentQuantity,
                  variant: { id: 'gid://shopify/ProductVariant/58792108065142' },
                  product: { id: PRODUCT_GID },
                  // GBP 0.00: the 100% code discount zeroed the line.
                  discountedUnitPriceAfterAllDiscountsSet: { shopMoney: { amount: '0.00' } },
                  discountedTotalSet: { shopMoney: { amount: '0.00' } },
                  customAttributes: [
                    { key: 'Skill answer', value: 'Bunker' },
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
          handle: 'qa-3-competition-closer-test',
          title: 'QA-3 — Competition Closer Test',
          status: 'ACTIVE',
          entriesTotal: { value: '20' },
          entryPrefix: { value: 'PUT' },
          entryStartNumber: { value: '1001' },
          skillQuestion: { value: 'What is a sand pit traditionally called in golf?' },
          skillAnswers: { value: JSON.stringify(['Rough', 'Bunker', 'Fairway']) },
          skillAnswerCorrect: { value: 'Bunker' },
          variants: { nodes: [{ id: 'gid://shopify/ProductVariant/58792108065142', entries: null }] },
        },
      },
    });
  };
}

function deps(currentQuantity: number) {
  return {
    db,
    shopify: new ShopifyClient({
      store: SHOP,
      accessToken: 't',
      logger: silentLogger(),
      fetchImpl: mockFetch(currentQuantity) as never,
      backoffBaseMs: 0,
    }),
    logger: silentLogger(),
    shopDomain: SHOP,
    runId: 'run-u2',
    actor: 'system:webhook' as const,
    now: () => NOW,
    webhookId: 'wh-u2',
  };
}

beforeEach(async () => {
  db = new TestD1();
  competitionId = seedCompetition(db, { capacity: 20 }).competitionId;
  await buildPool(db, competitionId, { prefix: 'PUT', startNumber: 1001, capacity: 20, padWidth: 4 });
});

describe('U2 — order #1009, quantity 5 partially refunded by 2', () => {
  test('the observed figures are internally consistent', () => {
    // quantity - refunded = currentQuantity = refundableQuantity.
    expect(ORDER_1009.quantity - ORDER_1009.refundedUnits).toBe(ORDER_1009.currentQuantity);
    expect(ORDER_1009.refundableQuantity).toBe(ORDER_1009.currentQuantity);
  });

  test('targetCount reads currentQuantity, giving 3', () => {
    expect(targetCount(ORDER_1009.currentQuantity, ORDER_1009.entriesPerUnit)).toBe(3);
  });

  test('the original quantity is NOT the entitlement signal', () => {
    // The guard. Were quantity the source, the target would be 5, the refund
    // would be invisible and two entry numbers would stay wrongly allocated.
    expect(targetCount(ORDER_1009.quantity, ORDER_1009.entriesPerUnit)).toBe(5);
    expect(targetCount(ORDER_1009.quantity, ORDER_1009.entriesPerUnit)).not.toBe(
      targetCount(ORDER_1009.currentQuantity, ORDER_1009.entriesPerUnit),
    );
  });

  test('end to end: 5 allocated, then 2 released from the top, leaving 3', async () => {
    await processOrder(deps(ORDER_1009.quantity), ORDER_GID, 'ORDER_EDIT');
    const key = await allocationId(SHOP, '13549712998774', '39027315573110');
    expect(await countHeld(db, competitionId, key)).toBe(5);

    // The refund webhook. Nothing about the order changed except the one
    // figure Shopify actually moved.
    const out = await processOrder(deps(ORDER_1009.currentQuantity), ORDER_GID, 'REFUND');

    expect(out[0]?.released).toEqual(['PUT1005', 'PUT1004']);
    expect(out[0]?.claimed).toEqual([]);
    expect((await listHeld(db, competitionId, key)).map((h) => h.entry_number)).toEqual([
      'PUT1001',
      'PUT1002',
      'PUT1003',
    ]);
    expect(await countHeld(db, competitionId, key)).toBe(ORDER_1009.currentQuantity);
  });

  test('the ledger keeps the original quantity but targets the current one', async () => {
    await processOrder(deps(ORDER_1009.quantity), ORDER_GID, 'ORDER_EDIT');
    await processOrder(deps(ORDER_1009.currentQuantity), ORDER_GID, 'REFUND');

    const key = await allocationId(SHOP, '13549712998774', '39027315573110');
    expect(db.query<Record<string, unknown>>(`SELECT * FROM allocation WHERE allocation_id=?`, key)[0]).toMatchObject({
      // Order history. Preserved, exactly as Shopify preserves `quantity`.
      ordered_quantity: ORDER_1009.quantity,
      entries_per_unit: ORDER_1009.entriesPerUnit,
      // Entitlement. Driven by currentQuantity.
      target_count: ORDER_1009.currentQuantity,
      held_count: ORDER_1009.currentQuantity,
    });
  });

  test('the two released numbers go back into the pool, lowest-first', async () => {
    await processOrder(deps(ORDER_1009.quantity), ORDER_GID, 'ORDER_EDIT');
    await processOrder(deps(ORDER_1009.currentQuantity), ORDER_GID, 'REFUND');

    // 20 in the pool, 3 still held -> 17 available, including PUT1004/PUT1005.
    expect(db.query<{ n: number }>(`SELECT COUNT(*) AS n FROM entry_number WHERE status='AVAILABLE'`)[0]).toMatchObject({
      n: 17,
    });
    const reissuable = db.query<{ entry_number: string }>(
      `SELECT entry_number FROM entry_number WHERE status='AVAILABLE' ORDER BY seq ASC LIMIT 2`,
    );
    expect(reissuable.map((r) => r.entry_number)).toEqual(['PUT1004', 'PUT1005']);
  });

  test('the release is driven by quantity alone, never by money or status', async () => {
    // #1009 refunded GBP 0.00 and created no transaction. A money-driven
    // release would never have fired; a status-driven one would not either,
    // because the order is still PAID and still uncancelled.
    expect(ORDER_1009.refundTransactions).toEqual([]);
    expect(ORDER_1009.totalRefunded).toBe('0.00');
    expect(ORDER_1009.displayFinancialStatus).toBe('PAID');
    expect(ORDER_1009.cancelledAt).toBeNull();

    await processOrder(deps(ORDER_1009.quantity), ORDER_GID, 'ORDER_EDIT');
    const out = await processOrder(deps(ORDER_1009.currentQuantity), ORDER_GID, 'REFUND');
    expect(out[0]?.released).toHaveLength(ORDER_1009.refundedUnits);
  });
});
