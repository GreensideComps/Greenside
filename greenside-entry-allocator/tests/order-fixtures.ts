/**
 * A mocked Shopify order for end-to-end processing tests: one line item on
 * competition 900001, shaped like the live orders the allocator was built
 * against. No network.
 */
import type { D1Like } from '../src/db';
import { ShopifyClient } from '../src/shopify';
import { NOW, silentLogger } from './helpers';

export const SHOP = 'test.myshopify.com';
export const PRODUCT_GID = 'gid://shopify/Product/900001';
export const ORDER = 'gid://shopify/Order/1042';

export interface OrderOpts {
  quantity: number;
  currentQuantity: number;
  cancelledAt?: string | null;
  answer?: string;
  correct?: string | null;
  entries?: string;
  financialStatus?: string;
}

export function mockFetch(o: OrderOpts) {
  return async (_url: string, init?: RequestInit): Promise<Response> => {
    const body = JSON.parse(String(init?.body ?? '{}')) as { query: string };
    if (body.query.includes('AllocatorOrder')) {
      return Response.json({
        data: {
          order: {
            id: ORDER, name: '#1042', createdAt: NOW, test: false, cancelledAt: o.cancelledAt ?? null,
            displayFinancialStatus: o.financialStatus ?? 'PAID', customer: { id: 'gid://shopify/Customer/7' },
            lineItems: {
              nodes: [{
                id: 'gid://shopify/LineItem/55', quantity: o.quantity, currentQuantity: o.currentQuantity,
                variant: { id: 'gid://shopify/ProductVariant/88' }, product: { id: PRODUCT_GID },
                discountedUnitPriceAfterAllDiscountsSet: { shopMoney: { amount: '2.49' } },
                discountedTotalSet: { shopMoney: { amount: '7.47' } },
                customAttributes: [
                  { key: 'Skill answer', value: o.answer ?? 'Bunker' },
                  { key: '_skill_question', value: 'What is a sand pit traditionally called in golf?' },
                  { key: '_entry_route', value: 'online' },
                ],
              }],
            },
          },
        },
      });
    }
    return Response.json({
      data: {
        product: {
          id: PRODUCT_GID, handle: 'win-a-putter', title: 'Win a TaylorMade Putter', status: 'ACTIVE',
          entriesTotal: { value: '20' }, entryPrefix: { value: 'PUT' }, entryStartNumber: { value: '1001' },
          skillQuestion: { value: 'What is a sand pit traditionally called in golf?' },
          skillAnswers: { value: JSON.stringify(['Rough', 'Bunker', 'Fairway']) },
          skillAnswerCorrect: o.correct === null ? null : { value: o.correct ?? 'Bunker' },
          variants: { nodes: [{ id: 'gid://shopify/ProductVariant/88', entries: o.entries ? { value: o.entries } : null }] },
        },
      },
    });
  };
}

export function deps(db: D1Like, o: OrderOpts, runId: string) {
  return {
    db, logger: silentLogger(), shopDomain: SHOP, runId, actor: 'system:webhook' as const, now: () => NOW, webhookId: `wh-${runId}`,
    shopify: new ShopifyClient({ store: SHOP, accessToken: 't', logger: silentLogger(), fetchImpl: mockFetch(o) as never, backoffBaseMs: 0 }),
  };
}
