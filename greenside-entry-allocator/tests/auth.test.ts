/**
 * Shopify access tokens by the client-credentials grant (src/auth.ts), and
 * ShopifyClient's use of them.
 *
 * Deterministic: the clock is injected and the network is a fake, so expiry
 * is exercised by moving the clock, never by waiting.
 */
import { beforeEach, describe, expect, test } from 'vitest';
import {
  ClientCredentialsTokenSource,
  checkScopes,
  resetTokenSources,
  TOKEN_REFRESH_MARGIN_MS,
  TokenError,
  tokenSourceFor,
} from '../src/auth';
import { Logger } from '../src/logging';
import { COMPETITION_QUERY, ORDER_QUERY, ShopifyClient, ShopifyError } from '../src/shopify';

const STORE = 'test.myshopify.com';
const CLIENT_ID = 'test-client-id';
const CLIENT_SECRET = 'test-client-secret-value';
const TOKEN_ENDPOINT = `https://${STORE}/admin/oauth/access_token`;
const GRAPHQL_ENDPOINT = `https://${STORE}/admin/api/2026-07/graphql.json`;
const DAY_SECONDS = 86399;

interface Call {
  url: string;
  body: Record<string, unknown>;
  token: string | null;
}

/** A fake Shopify: token endpoint and GraphQL endpoint, fully scripted. */
function fakeShopify(
  opts: {
    scope?: string;
    tokenStatus?: () => number;
    graphqlStatus?: (call: number) => number;
  } = {},
) {
  const calls: Call[] = [];
  let issued = 0;
  let graphqlCalls = 0;
  const fetchImpl = async (url: string, init?: RequestInit): Promise<Response> => {
    const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
    calls.push({ url, body, token: new Headers(init?.headers).get('X-Shopify-Access-Token') });
    if (url === TOKEN_ENDPOINT) {
      const status = opts.tokenStatus?.() ?? 200;
      if (status !== 200) return Response.json({ error: 'invalid_client' }, { status });
      issued++;
      return Response.json({ access_token: `shpat_token_${issued}`, scope: opts.scope ?? 'read_orders,read_products', expires_in: DAY_SECONDS });
    }
    graphqlCalls++;
    const status = opts.graphqlStatus?.(graphqlCalls) ?? 200;
    if (status !== 200) return new Response('{}', { status });
    const query = String(body['query']);
    if (query.includes('AllocatorOrder')) {
      return Response.json({ data: { order: { id: 'gid://shopify/Order/1042', name: '#1042', lineItems: { nodes: [] } } } });
    }
    return Response.json({ data: { product: { id: 'gid://shopify/Product/900001', handle: 'p', variants: { nodes: [] } } } });
  };
  return {
    fetchImpl: fetchImpl as unknown as typeof fetch,
    calls,
    exchanges: () => calls.filter((c) => c.url === TOKEN_ENDPOINT).length,
    graphql: () => calls.filter((c) => c.url === GRAPHQL_ENDPOINT),
  };
}

function clock(start = 1_800_000_000_000) {
  let t = start;
  return { now: () => t, advance: (ms: number) => (t += ms) };
}

function source(shop: ReturnType<typeof fakeShopify>, now = clock().now, creds = { clientId: CLIENT_ID, clientSecret: CLIENT_SECRET }) {
  return new ClientCredentialsTokenSource({ store: STORE, ...creds, fetchImpl: shop.fetchImpl, now });
}

function capturingLogger(): { logger: Logger; lines: string[] } {
  const lines: string[] = [];
  return { logger: new Logger({}, [CLIENT_SECRET], (line) => lines.push(line)), lines };
}

describe('token exchange', () => {
  test('a token is fetched when none is cached, by the client-credentials grant', async () => {
    const shop = fakeShopify();
    expect(await source(shop).getToken()).toBe('shpat_token_1');

    expect(shop.calls).toHaveLength(1);
    expect(shop.calls[0]).toMatchObject({
      url: TOKEN_ENDPOINT,
      body: { client_id: CLIENT_ID, client_secret: CLIENT_SECRET, grant_type: 'client_credentials' },
    });
  });

  test('the cached token is reused while valid', async () => {
    const shop = fakeShopify();
    const c = clock();
    const tokens = source(shop, c.now);
    await tokens.getToken();
    c.advance(23 * 60 * 60 * 1000);
    expect(await tokens.getToken()).toBe('shpat_token_1');
    expect(shop.exchanges()).toBe(1);
  });

  test('the token is refreshed a few minutes before it expires, not after', async () => {
    const shop = fakeShopify();
    const c = clock();
    const tokens = source(shop, c.now);
    await tokens.getToken();

    const refreshAt = DAY_SECONDS * 1000 - TOKEN_REFRESH_MARGIN_MS;
    c.advance(refreshAt - 1);
    expect(await tokens.getToken()).toBe('shpat_token_1');
    c.advance(1);
    expect(await tokens.getToken()).toBe('shpat_token_2');
    expect(shop.exchanges()).toBe(2);
  });

  test('concurrent requests share one token exchange', async () => {
    const shop = fakeShopify();
    const tokens = source(shop);
    const results = await Promise.all(Array.from({ length: 10 }, () => tokens.getToken()));
    expect(new Set(results)).toEqual(new Set(['shpat_token_1']));
    expect(shop.exchanges()).toBe(1);
  });

  test('invalidate drops only the rejected token', async () => {
    const shop = fakeShopify();
    const tokens = source(shop);
    const first = await tokens.getToken();
    tokens.invalidate('some-other-token');
    expect(await tokens.getToken()).toBe(first);
    tokens.invalidate(first);
    expect(await tokens.getToken()).toBe('shpat_token_2');
  });

  test('a failed exchange is surfaced, not cached, and carries no credential', async () => {
    let status = 400;
    const shop = fakeShopify({ tokenStatus: () => status });
    const tokens = source(shop);

    const err = await tokens.getToken().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(TokenError);
    expect(err).toMatchObject({ code: 'EXCHANGE_FAILED', status: 400 });
    expect(String((err as Error).message)).toContain('invalid_client');
    expect(String((err as Error).message)).not.toContain(CLIENT_SECRET);

    status = 200;
    expect(await tokens.getToken()).toBe('shpat_token_1');
    expect(shop.exchanges()).toBe(2);
  });

  test('a network failure or a malformed response is surfaced as EXCHANGE_FAILED', async () => {
    const down = new ClientCredentialsTokenSource({
      store: STORE, clientId: CLIENT_ID, clientSecret: CLIENT_SECRET,
      fetchImpl: (async () => { throw new Error('connection reset'); }) as unknown as typeof fetch,
    });
    await expect(down.getToken()).rejects.toMatchObject({ code: 'EXCHANGE_FAILED' });

    const malformed = new ClientCredentialsTokenSource({
      store: STORE, clientId: CLIENT_ID, clientSecret: CLIENT_SECRET,
      fetchImpl: (async () => Response.json({ scope: 'read_orders,read_products' })) as unknown as typeof fetch,
    });
    await expect(malformed.getToken()).rejects.toMatchObject({ code: 'EXCHANGE_FAILED' });
  });

  test('missing client ID or secret fails clearly, without calling Shopify', async () => {
    const shop = fakeShopify();
    await expect(source(shop, undefined, { clientId: '', clientSecret: CLIENT_SECRET }).getToken()).rejects.toMatchObject({
      code: 'MISSING_CREDENTIALS',
      message: 'client credentials missing: SHOPIFY_CLIENT_ID',
    });
    await expect(source(shop, undefined, { clientId: CLIENT_ID, clientSecret: '  ' }).getToken()).rejects.toMatchObject({
      code: 'MISSING_CREDENTIALS',
      message: 'client credentials missing: SHOPIFY_CLIENT_SECRET',
    });
    expect(shop.calls).toHaveLength(0);
  });
});

describe('granted scopes', () => {
  test('a token granted write_inventory is refused and not cached', async () => {
    const shop = fakeShopify({ scope: 'read_orders,read_products,write_inventory' });
    const tokens = source(shop);
    await expect(tokens.getToken()).rejects.toMatchObject({ code: 'FORBIDDEN_SCOPE' });
    await expect(tokens.getToken()).rejects.toMatchObject({ code: 'FORBIDDEN_SCOPE' });
    expect(shop.exchanges()).toBe(2);
  });

  test('the required read scopes must be present; a write scope implies its read', () => {
    expect(() => checkScopes('read_orders,read_products')).not.toThrow();
    expect(() => checkScopes('read_orders, write_products')).not.toThrow();
    expect(() => checkScopes('read_products')).toThrow(expect.objectContaining({ code: 'MISSING_SCOPE' }));
    expect(() => checkScopes('')).toThrow(expect.objectContaining({ code: 'MISSING_SCOPE' }));
    expect(() => checkScopes('write_inventory,read_orders,read_products')).toThrow(expect.objectContaining({ code: 'FORBIDDEN_SCOPE' }));
  });
});

describe('ShopifyClient through the token source', () => {
  function client(shop: ReturnType<typeof fakeShopify>, logger = capturingLogger().logger) {
    return new ShopifyClient({ store: STORE, tokens: source(shop), logger, fetchImpl: shop.fetchImpl, backoffBaseMs: 0 });
  }

  test('the existing queries work, sending the exchanged token; one exchange serves both', async () => {
    const shop = fakeShopify();
    const shopify = client(shop);
    expect(await shopify.fetchOrder('gid://shopify/Order/1042')).toMatchObject({ name: '#1042' });
    expect(await shopify.fetchCompetitionProduct('gid://shopify/Product/900001')).toMatchObject({ handle: 'p' });

    expect(shop.exchanges()).toBe(1);
    expect(shop.graphql().map((c) => [c.body['query'], c.token])).toEqual([
      [ORDER_QUERY, 'shpat_token_1'],
      [COMPETITION_QUERY, 'shpat_token_1'],
    ]);
  });

  test('a 401 causes exactly one fresh exchange and one retry', async () => {
    const shop = fakeShopify({ graphqlStatus: (n) => (n === 1 ? 401 : 200) });
    expect(await client(shop).fetchOrder('gid://shopify/Order/1042')).toMatchObject({ name: '#1042' });

    expect(shop.exchanges()).toBe(2);
    expect(shop.graphql().map((c) => c.token)).toEqual(['shpat_token_1', 'shpat_token_2']);
  });

  test('a second 401 is surfaced, not retried again', async () => {
    const shop = fakeShopify({ graphqlStatus: () => 401 });
    const err = await client(shop).fetchOrder('gid://shopify/Order/1042').catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ShopifyError);
    expect(err).toMatchObject({ kind: 'AUTH', status: 401, retryable: false });
    expect(shop.exchanges()).toBe(2);
    expect(shop.graphql()).toHaveLength(2);
  });

  test('a 403 is not an expired token: surfaced without a fresh exchange', async () => {
    const shop = fakeShopify({ graphqlStatus: () => 403 });
    await expect(client(shop).fetchOrder('gid://shopify/Order/1042')).rejects.toMatchObject({ kind: 'AUTH', status: 403 });
    expect(shop.exchanges()).toBe(1);
    expect(shop.graphql()).toHaveLength(1);
  });

  test('a failed token exchange is surfaced and no Admin API call is made', async () => {
    const shop = fakeShopify({ tokenStatus: () => 500 });
    await expect(client(shop).fetchOrder('gid://shopify/Order/1042')).rejects.toBeInstanceOf(TokenError);
    expect(shop.graphql()).toHaveLength(0);
  });

  test('a token granted write_inventory is never used', async () => {
    const shop = fakeShopify({ scope: 'read_orders,read_products,write_inventory' });
    await expect(client(shop).fetchOrder('gid://shopify/Order/1042')).rejects.toMatchObject({ code: 'FORBIDDEN_SCOPE' });
    expect(shop.graphql()).toHaveLength(0);
  });

  test('neither the client secret nor the access token is emitted by logging', async () => {
    const shop = fakeShopify({ graphqlStatus: (n) => (n === 1 ? 401 : 200) });
    const { logger, lines } = capturingLogger();
    await client(shop, logger).fetchOrder('gid://shopify/Order/1042');

    // Even a careless log call cannot leak them once the client has used the token.
    logger.info('careless', { note: `secret ${CLIENT_SECRET} tokens shpat_token_1 shpat_token_2` });
    const all = lines.join('\n');
    expect(lines.some((l) => l.includes('shopify_token_rejected'))).toBe(true);
    expect(all).not.toContain(CLIENT_SECRET);
    expect(all).not.toContain('shpat_token_1');
    expect(all).not.toContain('shpat_token_2');
    expect(all).toContain('[REDACTED]');
  });
});

describe('isolate-wide token cache', () => {
  beforeEach(() => resetTokenSources());

  test('the same store and credentials share one source; a rotated secret gets a new one', () => {
    const a = tokenSourceFor({ store: STORE, clientId: CLIENT_ID, clientSecret: CLIENT_SECRET });
    expect(tokenSourceFor({ store: STORE, clientId: CLIENT_ID, clientSecret: CLIENT_SECRET })).toBe(a);
    expect(tokenSourceFor({ store: STORE, clientId: CLIENT_ID, clientSecret: 'rotated-secret-value' })).not.toBe(a);
  });
});
