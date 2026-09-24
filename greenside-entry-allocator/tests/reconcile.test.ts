/**
 * The reconciliation sweep against a fake Shopify listing and real SQLite.
 *
 * The fake keeps one mutable list of orders. The listing and the full order
 * read both serve from it, so a test changes an order the way Shopify would
 * (refund, cancel) and then asks the sweep to notice. No webhook is sent for
 * the change: that is the missed delivery the sweep exists to recover.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { resetTokenSources } from '../src/auth';
import { SELECT_AUDIT_GAPS, SELECT_LEDGER_DRIFT } from '../src/db';
import worker, { type Env } from '../src/index';
import { Logger } from '../src/logging';
import { buildPool } from '../src/pool';
import { processOrder } from '../src/process';
import { reconcileSearch, runReconcile, type ReconcileOptions } from '../src/reconcile';
import { ShopifyClient } from '../src/shopify';
import { NOW, TestD1, seedCompetition, silentLogger, staticTokens } from './helpers';

const SHOP = 'test.myshopify.com';
const COMP = '900001';
const QUESTION = 'What is a sand pit traditionally called in golf?';

interface FakeLine {
  id: string;
  product: string | null;
  quantity: number;
  currentQuantity: number;
}

interface FakeOrder {
  id: string;
  financialStatus: string;
  cancelledAt: string | null;
  test: boolean;
  refunds: number;
  lines: FakeLine[];
  moreLines?: boolean;
  unreadable?: boolean;
  failing?: boolean;
}

function paidOrder(id: string, quantity: number, product: string = COMP): FakeOrder {
  return {
    id,
    financialStatus: 'PAID',
    cancelledAt: null,
    test: false,
    refunds: 0,
    lines: [{ id: `${id}0`, product, quantity, currentQuantity: quantity }],
  };
}

function listingNode(o: FakeOrder) {
  return {
    id: `gid://shopify/Order/${o.id}`,
    createdAt: NOW,
    updatedAt: NOW,
    test: o.test,
    cancelledAt: o.cancelledAt,
    displayFinancialStatus: o.financialStatus,
    refunds: o.refunds > 0 ? [{ id: `gid://shopify/Refund/${o.id}1` }] : [],
    lineItems: {
      pageInfo: { hasNextPage: o.moreLines ?? false },
      nodes: o.lines.map((l) => ({
        id: `gid://shopify/LineItem/${l.id}`,
        currentQuantity: l.currentQuantity,
        product: l.product ? { id: `gid://shopify/Product/${l.product}` } : null,
      })),
    },
  };
}

function orderNode(o: FakeOrder) {
  return {
    id: `gid://shopify/Order/${o.id}`,
    name: `#${o.id}`,
    createdAt: NOW,
    test: o.test,
    cancelledAt: o.cancelledAt,
    displayFinancialStatus: o.financialStatus,
    lineItems: {
      nodes: o.lines.map((l) => ({
        id: `gid://shopify/LineItem/${l.id}`,
        quantity: l.quantity,
        currentQuantity: l.currentQuantity,
        variant: { id: 'gid://shopify/ProductVariant/88' },
        product: l.product ? { id: `gid://shopify/Product/${l.product}` } : null,
        discountedUnitPriceAfterAllDiscountsSet: { shopMoney: { amount: '2.49' } },
        discountedTotalSet: { shopMoney: { amount: (2.49 * l.quantity).toFixed(2) } },
        customAttributes: [
          { key: 'Skill answer', value: 'Bunker' },
          { key: '_skill_question', value: QUESTION },
          { key: '_entry_route', value: 'online' },
        ],
      })),
    },
  };
}

function productNode() {
  return {
    id: `gid://shopify/Product/${COMP}`,
    handle: 'win-a-putter',
    title: 'Win a Putter',
    status: 'ACTIVE',
    entriesTotal: { value: '20' },
    entryPrefix: { value: 'PUT' },
    entryStartNumber: { value: '1001' },
    skillQuestion: { value: QUESTION },
    skillAnswers: { value: JSON.stringify(['Rough', 'Bunker', 'Fairway']) },
    skillAnswerCorrect: { value: 'Bunker' },
    variants: { nodes: [{ id: 'gid://shopify/ProductVariant/88', entries: null }] },
  };
}

class FakeShopify {
  orders: FakeOrder[] = [];
  pageSize = 25;
  listCalls: Array<{ search: string; sortKey: string; cursor: string | null }> = [];
  orderReads = 0;
  productReads = 0;
  /**
   * When > 0, product reads are held until this many are in flight, then
   * released together. A product is read only by a line that found NO
   * allocation, before its batch, so every caller released here has seen an
   * empty ledger and will try the first insert: the collision, made certain.
   */
  productBarrier = 0;
  private waiting: Array<() => void> = [];

  readonly fetch = async (_url: string, init?: RequestInit): Promise<Response> => {
    const body = JSON.parse(String(init?.body ?? '{}')) as { query: string; variables: Record<string, unknown> };
    if (body.query.includes('AllocatorReconcile')) return this.list(body.variables);
    if (body.query.includes('AllocatorOrder')) return this.order(String(body.variables['id']));
    this.productReads += 1;
    if (this.productBarrier > 0) {
      await new Promise<void>((resolve) => {
        this.waiting.push(resolve);
        if (this.waiting.length >= this.productBarrier) {
          for (const release of this.waiting) release();
          this.waiting = [];
          this.productBarrier = 0;
        }
      });
    }
    return Response.json({ data: { product: productNode() } });
  };

  private list(v: Record<string, unknown>): Response {
    const cursor = (v['cursor'] as string | null | undefined) ?? null;
    this.listCalls.push({ search: String(v['search']), sortKey: String(v['sortKey']), cursor });
    const start = cursor ? Number(cursor) : 0;
    const slice = this.orders.slice(start, start + this.pageSize);
    const end = start + slice.length;
    return Response.json({
      data: {
        orders: {
          pageInfo: { hasNextPage: end < this.orders.length, endCursor: slice.length > 0 ? String(end) : null },
          nodes: slice.map(listingNode),
        },
      },
    });
  }

  private order(gid: string): Response {
    this.orderReads += 1;
    const o = this.orders.find((x) => `gid://shopify/Order/${x.id}` === gid);
    if (o?.failing) return new Response('bad request', { status: 400 });
    if (!o || o.unreadable) return Response.json({ data: { order: null } });
    return Response.json({ data: { order: orderNode(o) } });
  }
}

let db: TestD1;
let shop: FakeShopify;
let logs: Array<Record<string, unknown>>;

beforeEach(async () => {
  db = new TestD1();
  seedCompetition(db, { competition_id: COMP, capacity: 20 });
  await buildPool(db, COMP, { prefix: 'PUT', startNumber: 1001, capacity: 20, padWidth: 4 });
  shop = new FakeShopify();
  logs = [];
});

function client(): ShopifyClient {
  return new ShopifyClient({
    store: SHOP,
    tokens: staticTokens(),
    logger: silentLogger(),
    fetchImpl: shop.fetch as never,
    backoffBaseMs: 0,
  });
}

function sweep(opts: Partial<ReconcileOptions> = {}, runId = 'sweep-1') {
  return runReconcile(
    {
      db,
      shopify: client(),
      logger: new Logger({}, [], (line) => logs.push(JSON.parse(line) as Record<string, unknown>)),
      shopDomain: SHOP,
      runId,
      now: () => NOW,
    },
    { mode: 'trailing', report: false, ...opts },
  );
}

/** The webhook path, used to build the state a later missed delivery drifts from. */
function webhook(orderId: string, runId = 'wh-1') {
  return processOrder(
    {
      db,
      shopify: client(),
      logger: silentLogger(),
      shopDomain: SHOP,
      runId,
      actor: 'system:webhook',
      now: () => NOW,
      webhookId: `webhook-${runId}`,
    },
    `gid://shopify/Order/${orderId}`,
    'ORDER_EDIT',
  );
}

function held(orderId?: string): string[] {
  const rows = db.query<{ entry_number: string; order_id: string }>(
    `SELECT entry_number, order_id FROM entry_number
      WHERE competition_id = ? AND status = 'ALLOCATED' ORDER BY seq`,
    COMP,
  );
  return rows.filter((r) => orderId === undefined || r.order_id === orderId).map((r) => r.entry_number);
}

function fingerprint(): string {
  return JSON.stringify([
    db.query('SELECT * FROM entry_number ORDER BY competition_id, seq'),
    db.query('SELECT * FROM allocation ORDER BY allocation_id'),
    db.query('SELECT * FROM entry_event ORDER BY id'),
  ]);
}

function integrity(): { gaps: number; drift: number } {
  return { gaps: db.query(SELECT_AUDIT_GAPS, COMP).length, drift: db.query(SELECT_LEDGER_DRIFT, COMP).length };
}

function events(where = '1 = 1', ...params: unknown[]) {
  return db.query<{
    event_type: string;
    entry_number: string | null;
    reason: string | null;
    actor: string;
    run_id: string;
    webhook_id: string | null;
    to_status: string | null;
    detail_json: string;
  }>(`SELECT * FROM entry_event WHERE ${where} ORDER BY id`, ...params);
}

function logged(event: string): Array<Record<string, unknown>> {
  return logs.filter((l) => l['event'] === event);
}

function freeze(): void {
  db.sqlite.exec(`UPDATE competition SET status = 'FROZEN', frozen_at = '${NOW}' WHERE competition_id = '${COMP}'`);
}

describe('search window and paging', () => {
  test('trailing and deep windows use Shopify search syntax and the matching sort key', () => {
    expect(reconcileSearch('trailing', Date.parse(NOW))).toEqual({
      search: "updated_at:>='2026-09-23T08:00:00Z'",
      sortKey: 'UPDATED_AT',
    });
    expect(reconcileSearch('deep', Date.parse(NOW))).toEqual({
      search: "created_at:>='2026-07-26T10:00:00Z'",
      sortKey: 'CREATED_AT',
    });
  });

  test('paging follows endCursor to the last page, with the same search on every page', async () => {
    shop.pageSize = 2;
    shop.orders = ['1', '2', '3', '4', '5'].map((id) => paidOrder(`30${id}`, 1, '777'));

    const s = await sweep({ mode: 'deep' });

    expect(shop.listCalls.map((c) => c.cursor)).toEqual([null, '2', '4']);
    expect(new Set(shop.listCalls.map((c) => `${c.sortKey} ${c.search}`))).toEqual(
      new Set(["CREATED_AT created_at:>='2026-07-26T10:00:00Z'"]),
    );
    expect(s).toMatchObject({ pages: 3, orders_seen: 5, in_scope: 0, truncated: false });
    expect(shop.orderReads).toBe(0);
  });
});

describe('recovery of missed deliveries', () => {
  test('a paid order with no allocation is claimed lowest-first, recorded as the reconciler', async () => {
    shop.orders = [paidOrder('2001', 2)];

    const s = await sweep();

    expect(held('2001')).toEqual(['PUT1001', 'PUT1002']);
    const allocated = events("event_type = 'ALLOCATED'");
    expect(allocated).toHaveLength(2);
    for (const e of allocated) {
      expect(e).toMatchObject({ actor: 'system:reconcile', run_id: 'sweep-1', webhook_id: null });
    }
    expect(db.query<{ source: string }>('SELECT source FROM allocation')).toEqual([{ source: 'reconcile' }]);
    expect(s).toMatchObject({ mismatched: 1, converged_orders: 1, claimed: 2, released: 0, errors: 0 });
    expect(integrity()).toEqual({ gaps: 0, drift: 0 });
  });

  test('a missed partial refund releases the highest number with reason REFUND', async () => {
    const order = paidOrder('2002', 2);
    shop.orders = [order];
    await webhook('2002');
    expect(held('2002')).toEqual(['PUT1001', 'PUT1002']);

    order.lines[0]!.currentQuantity = 1;
    order.financialStatus = 'PARTIALLY_REFUNDED';
    order.refunds = 1;
    const s = await sweep();

    expect(held('2002')).toEqual(['PUT1001']);
    expect(events("actor = 'system:reconcile' AND event_type = 'RELEASED'")).toEqual([
      expect.objectContaining({ entry_number: 'PUT1002', reason: 'REFUND', webhook_id: null }),
    ]);
    expect(events("actor = 'system:reconcile' AND event_type = 'RETURNED_TO_POOL'")).toEqual([
      expect.objectContaining({ entry_number: 'PUT1002', detail_json: JSON.stringify({ reason: 'REFUND' }) }),
    ]);
    expect(s).toMatchObject({ mismatched: 1, released: 1, claimed: 0 });
    expect(integrity()).toEqual({ gaps: 0, drift: 0 });
  });

  test('a missed cancellation releases everything with reason CANCELLED, even with a refund present', async () => {
    const order = paidOrder('2003', 2);
    shop.orders = [order];
    await webhook('2003');

    order.cancelledAt = NOW;
    order.lines[0]!.currentQuantity = 0;
    order.refunds = 1;
    const s = await sweep();

    expect(held('2003')).toEqual([]);
    const released = events("actor = 'system:reconcile' AND event_type = 'RELEASED'");
    expect(released.map((e) => [e.entry_number, e.reason])).toEqual([
      ['PUT1002', 'CANCELLED'],
      ['PUT1001', 'CANCELLED'],
    ]);
    expect(db.query<{ status: string; held_count: number }>('SELECT status, held_count FROM allocation')).toEqual([
      { status: 'RELEASED', held_count: 0 },
    ]);
    expect(s).toMatchObject({ released: 2 });
    expect(integrity()).toEqual({ gaps: 0, drift: 0 });
  });
});

describe('compare first', () => {
  test('an order that already matches the pool is not re-read and nothing is written', async () => {
    shop.orders = [paidOrder('2004', 2)];
    await webhook('2004');
    shop.orderReads = 0;
    shop.productReads = 0;
    const before = fingerprint();

    const s = await sweep();

    expect(shop.orderReads).toBe(0);
    expect(shop.productReads).toBe(0);
    expect(fingerprint()).toBe(before);
    expect(s).toMatchObject({ in_scope: 1, mismatched: 0 });
  });

  test('non-competition lines, test orders, DRAFT competitions and held (PENDING) orders are left alone', async () => {
    seedCompetition(db, { competition_id: '900002', prefix: 'DRF', status: 'DRAFT' });
    shop.orders = [
      paidOrder('2101', 1, '777'),
      { ...paidOrder('2102', 1), test: true },
      paidOrder('2103', 1, '900002'),
      { ...paidOrder('2104', 1), financialStatus: 'PENDING' },
    ];
    const before = fingerprint();

    const s = await sweep();

    expect(shop.orderReads).toBe(0);
    expect(fingerprint()).toBe(before);
    // 2102 and 2104 are competition orders, so in scope; neither drifts.
    expect(s).toMatchObject({ orders_seen: 4, in_scope: 2, mismatched: 0 });
  });

  test('an order with more lines than the listing shows is always re-read in full', async () => {
    shop.orders = [{ ...paidOrder('2105', 1), moreLines: true }];
    await webhook('2105');
    shop.orderReads = 0;

    const s = await sweep();

    expect(shop.orderReads).toBe(1);
    expect(held('2105')).toEqual(['PUT1001']);
    expect(s).toMatchObject({ mismatched: 1, converged_orders: 0 });
  });
});

describe('idempotency', () => {
  test('a second sweep over the same window changes nothing', async () => {
    shop.orders = [paidOrder('2201', 2), paidOrder('2202', 1)];
    await sweep();
    const after = fingerprint();
    shop.orderReads = 0;

    const again = await sweep({}, 'sweep-2');

    expect(fingerprint()).toBe(after);
    expect(shop.orderReads).toBe(0);
    expect(again).toMatchObject({ mismatched: 0, claimed: 0, released: 0 });
  });

  test('a webhook that lands between the listing and the sweep’s re-read leaves the sweep nothing to do', async () => {
    shop.orders = [paidOrder('2203', 2)];

    // Natural interleaving: the listing saw no allocation, the webhook then
    // allocated, and the sweep's own re-read converges to no change.
    const [swept, hooked] = await Promise.allSettled([sweep(), webhook('2203')]);

    expect(swept.status).toBe('fulfilled');
    expect(hooked.status).toBe('fulfilled');
    expect(held('2203')).toEqual(['PUT1001', 'PUT1002']);
    expect(events("event_type = 'ALLOCATED'")).toHaveLength(2);
    expect(db.query('SELECT allocation_id FROM allocation')).toHaveLength(1);
    expect(integrity()).toEqual({ gaps: 0, drift: 0 });
  });

  test('when a sweep and a webhook both reach an empty ledger first, one batch rolls back whole and nothing doubles', async () => {
    shop.orders = [paidOrder('2204', 2)];
    shop.productBarrier = 2;

    const [swept, hooked] = await Promise.allSettled([sweep(), webhook('2204')]);

    // Exactly one side loses the insert on the allocation's primary key: the
    // sweep counts it as an error and carries on; a webhook would return 500
    // and be retried by Shopify.
    const sweepErrors = swept.status === 'fulfilled' ? swept.value.errors : 1;
    const webhookFailures = hooked.status === 'rejected' ? 1 : 0;
    expect(sweepErrors + webhookFailures).toBe(1);
    const failure = swept.status === 'fulfilled' && swept.value.errors === 1
      ? String(logged('reconcile_order_failed')[0]?.['error'])
      : String((hooked as PromiseRejectedResult).reason?.message);
    expect(failure).toMatch(/UNIQUE|PRIMARY KEY|constraint/i);

    expect(held('2204')).toEqual(['PUT1001', 'PUT1002']);
    expect(events("event_type = 'ALLOCATED'")).toHaveLength(2);
    expect(db.query('SELECT allocation_id FROM allocation')).toHaveLength(1);
    expect(integrity()).toEqual({ gaps: 0, drift: 0 });

    const followUp = await sweep({}, 'sweep-3');
    expect(followUp).toMatchObject({ mismatched: 0, errors: 0 });
  });
});

describe('report mode (dry run)', () => {
  test('logs the drift, never re-reads an order and writes nothing', async () => {
    shop.orders = [paidOrder('2301', 2)];
    const before = fingerprint();

    const s = await sweep({ report: true });

    expect(fingerprint()).toBe(before);
    expect(shop.orderReads).toBe(0);
    expect(shop.productReads).toBe(0);
    const wouldChange = logged('reconcile_would_change');
    expect(wouldChange).toHaveLength(1);
    expect(wouldChange[0]).toMatchObject({
      order_gid: 'gid://shopify/Order/2301',
      lines: [expect.objectContaining({ action: 'CLAIM', competitionId: COMP })],
    });
    expect(s).toMatchObject({ report: true, mismatched: 1, claimed: 0, released: 0 });
  });
});

describe('frozen competitions', () => {
  test('a missed allocation after the freeze is reported, not claimed', async () => {
    freeze();
    shop.orders = [paidOrder('2401', 1)];
    const before = fingerprint();

    const s = await sweep();

    expect(shop.orderReads).toBe(0);
    expect(fingerprint()).toBe(before);
    expect(s).toMatchObject({ refused_not_open: 1, mismatched: 0 });
    expect(logged('reconcile_refused_not_open')).toEqual([
      expect.objectContaining({ order_gid: 'gid://shopify/Order/2401', status: 'FROZEN' }),
    ]);
  });

  test('a missed release after the freeze leaves the number RELEASED, never back in the pool', async () => {
    const order = paidOrder('2402', 1);
    shop.orders = [order];
    await webhook('2402');
    freeze();

    order.cancelledAt = NOW;
    order.lines[0]!.currentQuantity = 0;
    const s = await sweep();

    expect(db.query<{ status: string }>("SELECT status FROM entry_number WHERE entry_number = 'PUT1001'")).toEqual([
      { status: 'RELEASED' },
    ]);
    expect(events("actor = 'system:reconcile'").map((e) => [e.event_type, e.to_status, e.reason])).toEqual([
      ['RELEASED', 'RELEASED', 'CANCELLED'],
    ]);
    expect(s).toMatchObject({ released: 1 });
  });
});

describe('failure handling', () => {
  test('an unreadable order is reported and nothing is released', async () => {
    const order = paidOrder('2501', 2);
    shop.orders = [order];
    await webhook('2501');

    order.cancelledAt = NOW;
    order.lines[0]!.currentQuantity = 0;
    order.unreadable = true;
    const s = await sweep();

    expect(held('2501')).toEqual(['PUT1001', 'PUT1002']);
    expect(s).toMatchObject({ mismatched: 1, unreadable: 1, released: 0 });
    expect(logged('order_unreadable')).toEqual([
      expect.objectContaining({ order_gid: 'gid://shopify/Order/2501' }),
    ]);
  });

  test('one failing order does not stop the rest of the sweep', async () => {
    shop.orders = [{ ...paidOrder('2601', 1), failing: true }, paidOrder('2602', 1)];

    const s = await sweep();

    expect(held('2601')).toEqual([]);
    expect(held('2602')).toEqual(['PUT1001']);
    expect(s).toMatchObject({ errors: 1, claimed: 1 });
    expect(logged('reconcile_order_failed')).toEqual([
      expect.objectContaining({ order_gid: 'gid://shopify/Order/2601' }),
    ]);
  });

  test('the page budget ends the run cleanly and says so', async () => {
    shop.pageSize = 1;
    shop.orders = ['1', '2', '3'].map((id) => paidOrder(`270${id}`, 1, '777'));

    const s = await sweep({ budget: { maxPages: 2, maxConverge: 10 } });

    expect(s).toMatchObject({ pages: 2, truncated: true });
    expect(logged('reconcile_truncated')).toHaveLength(1);
  });

  test('the convergence budget ends the run cleanly; the remainder waits for the next run', async () => {
    shop.orders = [paidOrder('2801', 1), paidOrder('2802', 1), paidOrder('2803', 1)];

    const s = await sweep({ budget: { maxPages: 10, maxConverge: 1 } });

    expect(held()).toEqual(['PUT1001']);
    expect(s).toMatchObject({ converged_orders: 1, truncated: true });

    const next = await sweep({}, 'sweep-2');
    expect(next).toMatchObject({ converged_orders: 2 });
    expect(held()).toHaveLength(3);
  });
});

describe('aged allocations', () => {
  test('numbers held on an order near the 60-day read horizon in an OPEN competition are reported, not touched', async () => {
    shop.orders = [paidOrder('2901', 1)];
    await webhook('2901');
    db.sqlite.exec("UPDATE allocation SET order_created_at = '2026-07-01T00:00:00Z'");
    shop.orders = [];
    const before = fingerprint();

    const s = await sweep();

    expect(fingerprint()).toBe(before);
    expect(s).toMatchObject({ aged_held: 1 });
    expect(logged('reconcile_aged_allocation')).toEqual([
      expect.objectContaining({ order_id: '2901', held: 1 }),
    ]);
  });

  test('an equally old holding in a FROZEN competition is not warned on: numbers stay held after the draw', async () => {
    shop.orders = [paidOrder('2902', 1)];
    await webhook('2902');
    db.sqlite.exec("UPDATE allocation SET order_created_at = '2026-07-01T00:00:00Z'");
    freeze();
    shop.orders = [];
    const before = fingerprint();

    const s = await sweep();

    expect(held('2902')).toEqual(['PUT1001']);
    expect(fingerprint()).toBe(before);
    expect(s).toMatchObject({ aged_held: 0 });
    expect(logged('reconcile_aged_allocation')).toEqual([]);
  });
});

describe('scheduled() dispatch', () => {
  const CLIENT_SECRET = 'client-secret-value-for-tests';
  const ACCESS_TOKEN = 'shpat_reconcile_test_access_token';
  let lines: string[];

  beforeEach(() => {
    lines = [];
    vi.spyOn(console, 'log').mockImplementation((line: unknown) => {
      lines.push(String(line));
    });
    resetTokenSources();
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      if (String(url).endsWith('/admin/oauth/access_token')) {
        return Response.json({ access_token: ACCESS_TOKEN, scope: 'read_orders,read_products', expires_in: 86399 });
      }
      return shop.fetch(String(url), init);
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    resetTokenSources();
  });

  function env(extra: Partial<Env> = {}): Env {
    return {
      DB: db,
      SHOPIFY_STORE: SHOP,
      SHOPIFY_CLIENT_ID: 'client-id-value-for-tests',
      SHOPIFY_CLIENT_SECRET: CLIENT_SECRET,
      SHOPIFY_WEBHOOK_SECRET: CLIENT_SECRET,
      ALLOWED_SHOP_DOMAIN: SHOP,
      SHOPIFY_API_VERSION: '2026-07',
      ...extra,
    };
  }

  function run(cron: string, e: Env): Promise<void> {
    return worker.scheduled({ cron, scheduledTime: Date.parse(NOW), noRetry() {} } as ScheduledController, e);
  }

  function started(): Record<string, unknown> {
    return JSON.parse(lines.find((l) => l.includes('"reconcile_started"')) ?? '{}') as Record<string, unknown>;
  }

  test('the deep cron runs the deep sweep, and dry run means report mode', async () => {
    shop.orders = [paidOrder('3101', 2)];
    const before = fingerprint();

    await run('0 3 * * *', env());

    expect(started()).toMatchObject({ mode: 'deep', report: true });
    expect(shop.listCalls[0]?.sortKey).toBe('CREATED_AT');
    expect(shop.listCalls[0]?.search.startsWith("created_at:>='")).toBe(true);
    expect(fingerprint()).toBe(before);
  });

  test('any other cron runs the trailing sweep', async () => {
    await run('*/15 * * * *', env());

    expect(started()).toMatchObject({ mode: 'trailing', report: true });
    expect(shop.listCalls[0]?.sortKey).toBe('UPDATED_AT');
  });

  test('RECONCILE_DEEP_CRON chooses which cron is deep', async () => {
    await run('20,50 * * * *', env({ RECONCILE_DEEP_CRON: '20,50 * * * *' }));
    expect(started()).toMatchObject({ mode: 'deep' });

    lines = [];
    await run('0 3 * * *', env({ RECONCILE_DEEP_CRON: '20,50 * * * *' }));
    expect(started()).toMatchObject({ mode: 'trailing' });
  });

  test('live mode converges a drifted order', async () => {
    shop.orders = [paidOrder('3102', 2)];

    await run('*/15 * * * *', env({ DRY_RUN: 'false' }));

    expect(started()).toMatchObject({ mode: 'trailing', report: false });
    expect(held('3102')).toEqual(['PUT1001', 'PUT1002']);
  });

  test('missing configuration is logged and does not throw or call Shopify', async () => {
    await expect(run('*/15 * * * *', env({ SHOPIFY_STORE: undefined }))).resolves.toBeUndefined();

    expect(lines.some((l) => l.includes('"missing_configuration"') && l.includes('SHOPIFY_STORE'))).toBe(true);
    expect(shop.listCalls).toHaveLength(0);
  });

  test('no log line carries the client secret or the access token', async () => {
    shop.orders = [paidOrder('3103', 1), { ...paidOrder('3104', 1), failing: true }];

    await run('*/15 * * * *', env({ DRY_RUN: 'false' }));

    expect(lines.length).toBeGreaterThan(0);
    for (const line of lines) {
      expect(line).not.toContain(CLIENT_SECRET);
      expect(line).not.toContain(ACCESS_TOKEN);
    }
  });
});
