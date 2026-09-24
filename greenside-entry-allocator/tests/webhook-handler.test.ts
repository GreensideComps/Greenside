/**
 * The webhook handler end to end: a signed delivery goes through the Worker's
 * fetch() exactly as Shopify would send it, with only the Admin API mocked.
 *
 * Regression cover for orders/edited, whose payload wraps the edit rather
 * than the order -- { "order_edit": { "id": <edit>, "order_id": <order> } } --
 * and was once read as if it had a top-level id, so every edit was dropped as
 * webhook_no_order_id.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { resetTokenSources } from '../src/auth';
import worker, { TOPIC_ROUTES, type Env } from '../src/index';
import { computeHmac } from '../src/hmac';
import { buildPool } from '../src/pool';
import { NOW, TestD1, seedCompetition } from './helpers';
import { ORDER, SHOP, mockFetch } from './order-fixtures';

const SECRET = 'test-webhook-secret';

/** Shopify's orders/edited payload shape, for order 1042 (edit id deliberately different). */
const ORDERS_EDITED_BODY = JSON.stringify({
  order_edit: {
    id: 78912,
    app_id: null,
    created_at: NOW,
    committed_at: NOW,
    notify_customer: false,
    order_id: 1042,
    staff_note: '',
    user_id: null,
    line_items: { additions: [{ id: 55, delta: 1 }], removals: [] },
    discounts: { line_item: { additions: [], removals: [] } },
    shipping_lines: { additions: [], removals: [] },
  },
});

let db: TestD1;
let logLines: string[];
let shopifyRequests: Array<{ url: string; query: string; variables: Record<string, unknown>; token: string | null }>;
let tokenExchanges: number;
let tokenResponse: () => Response;

const CLIENT_SECRET = 'test-client-secret-value';
const ACCESS_TOKEN = 'shpat_test_access_token_value';

function env(overrides: Partial<Env> = {}): Env {
  return {
    DB: db,
    SHOPIFY_STORE: SHOP,
    SHOPIFY_CLIENT_ID: 'test-client-id',
    SHOPIFY_CLIENT_SECRET: CLIENT_SECRET,
    SHOPIFY_WEBHOOK_SECRET: SECRET,
    SHOPIFY_API_VERSION: '2026-07',
    ALLOWED_SHOP_DOMAIN: SHOP,
    DRY_RUN: 'false',
    ...overrides,
  };
}

async function deliver(
  topic: string,
  body: string,
  e: Env,
  headerOverrides: Record<string, string> = {},
): Promise<Response> {
  const route = TOPIC_ROUTES[topic];
  if (!route) throw new Error(`no route for ${topic}`);
  const request = new Request(`https://allocator.test${route.path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Shopify-Hmac-Sha256': await computeHmac(body, SECRET),
      'X-Shopify-Shop-Domain': SHOP,
      'X-Shopify-Topic': topic,
      'X-Shopify-Webhook-Id': 'wh-edit-1',
      ...headerOverrides,
    },
    body,
  });
  return worker.fetch(request, e);
}

function events(): string[] {
  return logLines.map((line) => (JSON.parse(line) as { event: string }).event);
}

beforeEach(async () => {
  db = new TestD1();
  const competitionId = seedCompetition(db, { capacity: 20 }).competitionId;
  await buildPool(db, competitionId, { prefix: 'PUT', startNumber: 1001, capacity: 20, padWidth: 4 });

  logLines = [];
  vi.spyOn(console, 'log').mockImplementation((line: unknown) => {
    logLines.push(String(line));
  });

  // The Worker caches tokens for the life of the isolate; start each test cold.
  resetTokenSources();
  tokenExchanges = 0;
  tokenResponse = () => Response.json({ access_token: ACCESS_TOKEN, scope: 'read_orders,read_products', expires_in: 86399 });

  shopifyRequests = [];
  const shopify = mockFetch({ quantity: 3, currentQuantity: 3 });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    if (String(url) === `https://${SHOP}/admin/oauth/access_token`) {
      tokenExchanges++;
      return tokenResponse();
    }
    const body = JSON.parse(String(init?.body ?? '{}')) as { query: string; variables: Record<string, unknown> };
    const token = new Headers(init?.headers).get('X-Shopify-Access-Token');
    shopifyRequests.push({ url: String(url), query: body.query, variables: body.variables, token });
    return shopify(url, init);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('orders/edited webhook', () => {
  test("Shopify's order_edit.order_id payload reaches normal order processing", async () => {
    const response = await deliver('orders/edited', ORDERS_EDITED_BODY, env());

    expect(response.status).toBe(200);
    const result = (await response.json()) as { status: string; outcomes: Array<{ action: string; claimed: string[] }> };
    expect(result.status).toBe('ok');

    // The canonical order was re-read by order_edit.order_id, not the edit id.
    const orderReads = shopifyRequests.filter((r) => r.query.includes('AllocatorOrder'));
    expect(orderReads).toHaveLength(1);
    expect(orderReads[0]!.variables).toEqual({ id: ORDER });
    expect(orderReads[0]!.url).toBe(`https://${SHOP}/admin/api/2026-07/graphql.json`);
    expect(orderReads[0]!.query).not.toMatch(/customer/i);

    // And the normal convergence ran on it.
    expect(result.outcomes).toHaveLength(1);
    expect(result.outcomes[0]!.claimed).toEqual(['PUT1001', 'PUT1002', 'PUT1003']);
    expect(db.query('SELECT order_id FROM allocation')).toEqual([{ order_id: '1042' }]);

    expect(events()).toContain('webhook_processed');
    expect(events()).not.toContain('webhook_no_order_id');
    const processed = logLines.map((l) => JSON.parse(l) as Record<string, unknown>).find((l) => l['event'] === 'webhook_processed');
    expect(processed).toMatchObject({ topic: 'orders/edited', order_gid: ORDER });
  });

  test('in dry run the edit is resolved to its order but nothing is read or written', async () => {
    const response = await deliver('orders/edited', ORDERS_EDITED_BODY, env({ DRY_RUN: 'true' }));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: 'dry_run', topic: 'orders/edited', order: ORDER });
    expect(shopifyRequests).toHaveLength(0);
    expect(db.query('SELECT COUNT(*) AS n FROM allocation')).toEqual([{ n: 0 }]);
  });

  test('a top-level id is not mistaken for the order', async () => {
    const response = await deliver('orders/edited', JSON.stringify({ id: 1042 }), env());

    expect(response.status).toBe(200);
    expect(events()).toContain('webhook_no_order_id');
    expect(shopifyRequests).toHaveLength(0);
    expect(db.query('SELECT COUNT(*) AS n FROM allocation')).toEqual([{ n: 0 }]);
  });

  test('HMAC, shop and topic are still checked before the payload is read', async () => {
    const forged = await deliver('orders/edited', ORDERS_EDITED_BODY, env(), {
      'X-Shopify-Hmac-Sha256': await computeHmac(ORDERS_EDITED_BODY, 'wrong-secret'),
    });
    const wrongShop = await deliver('orders/edited', ORDERS_EDITED_BODY, env(), {
      'X-Shopify-Shop-Domain': 'attacker.myshopify.com',
    });
    const wrongTopic = await deliver('orders/edited', ORDERS_EDITED_BODY, env(), {
      'X-Shopify-Topic': 'orders/paid',
    });

    expect([forged.status, wrongShop.status, wrongTopic.status]).toEqual([401, 401, 401]);
    expect(shopifyRequests).toHaveLength(0);
    expect(db.query('SELECT COUNT(*) AS n FROM allocation')).toEqual([{ n: 0 }]);
  });
});

describe('Shopify authentication through the webhook handler', () => {
  const body = JSON.stringify({ id: 1042 });

  test('a paid order is processed with a client-credentials token, fetched once and sent on every query', async () => {
    const response = await deliver('orders/paid', body, env());
    expect(response.status).toBe(200);

    expect(tokenExchanges).toBe(1);
    expect(shopifyRequests.length).toBeGreaterThan(0);
    const tokensSent = new Set(shopifyRequests.map((r) => r.token));
    expect(tokensSent).toEqual(new Set([ACCESS_TOKEN]));
    expect(db.query(`SELECT COUNT(*) AS n FROM entry_number WHERE status = 'ALLOCATED'`)).toEqual([{ n: 3 }]);

    // A second delivery in the same isolate reuses the cached token.
    await deliver('orders/create', body, env());
    expect(tokenExchanges).toBe(1);
  });

  test('missing client credentials fail clearly with 500 and no Shopify call', async () => {
    const response = await deliver('orders/paid', body, env({ SHOPIFY_CLIENT_ID: undefined, SHOPIFY_CLIENT_SECRET: '' }));

    expect(response.status).toBe(500);
    expect(await response.text()).toBe('Not configured');
    const missing = logLines.map((l) => JSON.parse(l) as Record<string, unknown>).find((l) => l['event'] === 'missing_configuration');
    expect(missing?.['missing']).toEqual(['SHOPIFY_CLIENT_ID', 'SHOPIFY_CLIENT_SECRET']);
    expect(tokenExchanges).toBe(0);
    expect(shopifyRequests).toHaveLength(0);
  });

  test('a failed token exchange is a 500, so Shopify redelivers; nothing is written', async () => {
    tokenResponse = () => Response.json({ error: 'invalid_client' }, { status: 400 });
    const response = await deliver('orders/paid', body, env());

    expect(response.status).toBe(500);
    expect(tokenExchanges).toBe(1);
    expect(shopifyRequests).toHaveLength(0);
    expect(db.query(`SELECT COUNT(*) AS n FROM allocation`)).toEqual([{ n: 0 }]);
    expect(events()).toContain('webhook_failed');
  });

  test('a token granted write_inventory is refused: 500, no Admin API call, nothing written', async () => {
    tokenResponse = () =>
      Response.json({ access_token: ACCESS_TOKEN, scope: 'read_orders,read_products,write_inventory', expires_in: 86399 });
    const response = await deliver('orders/paid', body, env());

    expect(response.status).toBe(500);
    expect(shopifyRequests).toHaveLength(0);
    expect(db.query(`SELECT COUNT(*) AS n FROM allocation`)).toEqual([{ n: 0 }]);
  });

  test('neither the client secret nor the access token reaches a log line', async () => {
    // Force the paths that log: a rejected token, then a failed exchange.
    let graphqlCalls = 0;
    const shopify = mockFetch({ quantity: 3, currentQuantity: 3 });
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      if (String(url).endsWith('/admin/oauth/access_token')) {
        tokenExchanges++;
        return tokenExchanges === 1
          ? tokenResponse()
          : Response.json({ error: 'invalid_client', detail: CLIENT_SECRET }, { status: 401 });
      }
      graphqlCalls++;
      if (graphqlCalls === 1) return new Response('{}', { status: 401 });
      return shopify(url, init);
    });

    const response = await deliver('orders/paid', body, env());
    expect(response.status).toBe(500);
    expect(events()).toEqual(expect.arrayContaining(['shopify_token_rejected', 'webhook_failed']));

    const all = logLines.join('\n');
    expect(all).not.toContain(CLIENT_SECRET);
    expect(all).not.toContain(ACCESS_TOKEN);
  });
});
