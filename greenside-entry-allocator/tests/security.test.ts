import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, test } from 'vitest';
import { computeHmac, timingSafeEqual, verifyWebhook } from '../src/hmac';
import { allocationId } from '../src/idempotency';
import { isDryRun, orderGidFromPayload, TOPIC_ROUTES } from '../src/index';
import { ORDER_QUERY } from '../src/shopify';

const SECRET = 'test-webhook-secret';
const SHOP = 'test.myshopify.com';

async function headersFor(body: string, over: Record<string, string | null> = {}): Promise<Headers> {
  const h = new Headers();
  const defaults: Record<string, string> = {
    'X-Shopify-Hmac-Sha256': await computeHmac(body, SECRET),
    'X-Shopify-Shop-Domain': SHOP,
    'X-Shopify-Topic': 'orders/paid',
    'X-Shopify-Webhook-Id': 'wh-1',
  };
  for (const [k, v] of Object.entries({ ...defaults, ...over })) if (v !== null) h.set(k, v);
  return h;
}

describe('HMAC verification', () => {
  const body = JSON.stringify({ id: 1042 });

  test('a correctly signed webhook is accepted', async () => {
    const v = await verifyWebhook({ rawBody: body, headers: await headersFor(body), secret: SECRET, allowedShopDomain: SHOP, expectedTopic: 'orders/paid' });
    expect(v.ok).toBe(true);
    expect(v.webhookId).toBe('wh-1');
  });

  test('an invalid signature is rejected', async () => {
    const h = await headersFor(body, { 'X-Shopify-Hmac-Sha256': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=' });
    const v = await verifyWebhook({ rawBody: body, headers: h, secret: SECRET, allowedShopDomain: SHOP });
    expect(v).toMatchObject({ ok: false, rejection: 'BAD_HMAC' });
  });

  test('a missing signature is rejected', async () => {
    const h = await headersFor(body, { 'X-Shopify-Hmac-Sha256': null });
    expect(await verifyWebhook({ rawBody: body, headers: h, secret: SECRET, allowedShopDomain: SHOP })).toMatchObject({
      ok: false, rejection: 'MISSING_HMAC',
    });
  });

  test('a signature computed with the wrong secret is rejected', async () => {
    const h = await headersFor(body, { 'X-Shopify-Hmac-Sha256': await computeHmac(body, 'wrong-secret') });
    expect(await verifyWebhook({ rawBody: body, headers: h, secret: SECRET, allowedShopDomain: SHOP })).toMatchObject({
      ok: false, rejection: 'BAD_HMAC',
    });
  });

  test('THE RAW BODY IS WHAT IS SIGNED: a re-serialised body fails', async () => {
    // Parsing and re-stringifying changes key order and whitespace. This is
    // why every caller must verify the exact text it received.
    const signed = '{"id":1042,"note":"x"}';
    const reserialised = JSON.stringify(JSON.parse('{"note":"x","id":1042}'));
    const h = await headersFor(signed);
    expect(await verifyWebhook({ rawBody: reserialised, headers: h, secret: SECRET, allowedShopDomain: SHOP })).toMatchObject({
      ok: false, rejection: 'BAD_HMAC',
    });
  });

  test('a single changed byte in the body fails', async () => {
    const h = await headersFor(body);
    expect(await verifyWebhook({ rawBody: JSON.stringify({ id: 1043 }), headers: h, secret: SECRET, allowedShopDomain: SHOP })).toMatchObject({
      ok: false, rejection: 'BAD_HMAC',
    });
  });
});

describe('shop domain validation', () => {
  const body = JSON.stringify({ id: 1 });

  test('a webhook from another shop is rejected even with a valid signature', async () => {
    const h = await headersFor(body, { 'X-Shopify-Shop-Domain': 'attacker.myshopify.com' });
    expect(await verifyWebhook({ rawBody: body, headers: h, secret: SECRET, allowedShopDomain: SHOP })).toMatchObject({
      ok: false, rejection: 'WRONG_SHOP',
    });
  });

  test('a missing shop domain is rejected', async () => {
    const h = await headersFor(body, { 'X-Shopify-Shop-Domain': null });
    expect(await verifyWebhook({ rawBody: body, headers: h, secret: SECRET, allowedShopDomain: SHOP })).toMatchObject({
      ok: false, rejection: 'MISSING_SHOP_DOMAIN',
    });
  });

  test('the shop comparison is case-insensitive', async () => {
    const h = await headersFor(body, { 'X-Shopify-Shop-Domain': 'TEST.myshopify.com' });
    expect((await verifyWebhook({ rawBody: body, headers: h, secret: SECRET, allowedShopDomain: SHOP })).ok).toBe(true);
  });

  test('a mismatched topic is rejected', async () => {
    const h = await headersFor(body, { 'X-Shopify-Topic': 'orders/cancelled' });
    expect(await verifyWebhook({ rawBody: body, headers: h, secret: SECRET, allowedShopDomain: SHOP, expectedTopic: 'orders/paid' })).toMatchObject({
      ok: false, rejection: 'TOPIC_MISMATCH',
    });
  });

  test('the signature is checked BEFORE the shop, so a forged body is never inspected', async () => {
    const h = await headersFor(body, {
      'X-Shopify-Hmac-Sha256': 'bad',
      'X-Shopify-Shop-Domain': 'attacker.myshopify.com',
    });
    expect((await verifyWebhook({ rawBody: body, headers: h, secret: SECRET, allowedShopDomain: SHOP })).rejection).toBe('BAD_HMAC');
  });
});

describe('constant-time comparison', () => {
  test('equal strings compare true', () => expect(timingSafeEqual('abcdef', 'abcdef')).toBe(true));
  test('different lengths compare false', () => expect(timingSafeEqual('abc', 'abcd')).toBe(false));
  test('a single differing character compares false', () => expect(timingSafeEqual('abcdef', 'abcdeg')).toBe(false));
});

describe('no replay-window rejection', () => {
  test('verification does not consider any timestamp', async () => {
    // Shopify retries for up to 48 hours. A freshness window would reject
    // exactly the legitimate retries the design depends on.
    // Comments are stripped: hmac.ts documents in prose WHY there is no
    // freshness window, and that prose must not fail its own assertion.
    const code = readFileSync(new URL('../src/hmac.ts', import.meta.url).pathname, 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '');
    expect(code).not.toMatch(/Date\.now|getTime|maxAgeMs|freshness/);
  });
});

describe('THE ALLOCATOR HAS NO INVENTORY-WRITE CAPABILITY', () => {
  const srcDir = new URL('../src', import.meta.url).pathname;
  const files = readdirSync(srcDir).filter((f: string) => f.endsWith('.ts'));

  test('no source file contains an inventory mutation', () => {
    const offenders: string[] = [];
    for (const file of files) {
      const body = readFileSync(join(srcDir, file), 'utf8');
      // Strip comments: shopify.ts documents what was removed.
      const code = body.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
      if (/inventorySetQuantities|inventoryAdjustQuantities|inventoryActivate|setAvailableQuantity/.test(code)) {
        offenders.push(file);
      }
    }
    expect(offenders).toEqual([]);
  });

  test('no source file contains a product or tag mutation', () => {
    const offenders: string[] = [];
    for (const file of files) {
      const body = readFileSync(join(srcDir, file), 'utf8');
      const code = body.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
      if (/tagsAdd|tagsRemove|productUpdate|productVariantsBulkUpdate|publishablePublish/.test(code)) {
        offenders.push(file);
      }
    }
    expect(offenders).toEqual([]);
  });

  test('the ONLY GraphQL mutation in the whole service is the order-metafield mirror', () => {
    const mutations: string[] = [];
    for (const file of files) {
      const body = readFileSync(join(srcDir, file), 'utf8');
      for (const m of body.matchAll(/mutation\s+(\w+)/g)) mutations.push(`${file}:${m[1]}`);
    }
    expect(mutations).toEqual(['shopify.ts:AllocatorMirror']);
  });

  test('no order-state mutation exists (cancel, refund, close)', () => {
    for (const file of files) {
      const code = readFileSync(join(srcDir, file), 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/^\s*\/\/.*$/gm, '');
      expect(code).not.toMatch(/orderCancel|refundCreate|orderClose|orderMarkAsPaid|draftOrderComplete/);
    }
  });

  test('no draw capability exists anywhere in the service', () => {
    // The draw is a separate project. Nothing here may pre-empt it.
    for (const file of files) {
      const code = readFileSync(join(srcDir, file), 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/^\s*\/\/.*$/gm, '');
      expect(code).not.toMatch(/drand|randomBeacon|selectWinner|commitmentHash|HMAC_DRBG|rejectionSampling/i);
    }
  });
});

describe('Shopify scopes', () => {
  test('ORDER_QUERY requests no customer field, which would need read_customers', () => {
    // The app holds read_orders and read_products only. Requesting
    // Order.customer fails the whole query with ACCESS_DENIED.
    expect(ORDER_QUERY).not.toMatch(/customer/i);
  });
});

describe('dry run', () => {
  test('only the exact string "false" disables it', () => {
    expect(isDryRun({} as never)).toBe(true);
    expect(isDryRun({ DRY_RUN: 'true' } as never)).toBe(true);
    expect(isDryRun({ DRY_RUN: '' } as never)).toBe(true);
    expect(isDryRun({ DRY_RUN: 'FALSE' } as never)).toBe(true);
    expect(isDryRun({ DRY_RUN: 'no' } as never)).toBe(true);
    expect(isDryRun({ DRY_RUN: 'false' } as never)).toBe(false);
  });
});

describe('idempotency key', () => {
  test('is deterministic for the same order and line', async () => {
    const a = await allocationId(SHOP, '1042', 'l1');
    const b = await allocationId(SHOP, '1042', 'l1');
    expect(a).toBe(b);
    expect(a).toMatch(/^[0-9a-f]{64}$/);
  });

  test('differs per line item, so one order can hold several competitions', async () => {
    expect(await allocationId(SHOP, '1042', 'l1')).not.toBe(await allocationId(SHOP, '1042', 'l2'));
  });

  test('differs per order and per shop', async () => {
    expect(await allocationId(SHOP, '1042', 'l1')).not.toBe(await allocationId(SHOP, '1043', 'l1'));
    expect(await allocationId(SHOP, '1042', 'l1')).not.toBe(await allocationId('other.myshopify.com', '1042', 'l1'));
  });
});

describe('webhook routing', () => {
  test('exactly the five agreed topics are routed, and disputes are absent', () => {
    expect(Object.keys(TOPIC_ROUTES).sort()).toEqual([
      'orders/cancelled', 'orders/create', 'orders/edited', 'orders/paid', 'refunds/create',
    ]);
    expect(Object.keys(TOPIC_ROUTES).join(' ')).not.toMatch(/dispute/);
  });

  test('refunds/create reads order_id; order topics read id', () => {
    expect(orderGidFromPayload('refunds/create', { id: 999, order_id: 1042 })).toBe('gid://shopify/Order/1042');
    expect(orderGidFromPayload('orders/paid', { id: 1042 })).toBe('gid://shopify/Order/1042');
    expect(orderGidFromPayload('orders/paid', {})).toBeNull();
    expect(orderGidFromPayload('orders/create', { id: 1042 })).toBe('gid://shopify/Order/1042');
    expect(orderGidFromPayload('orders/cancelled', { id: 1042 })).toBe('gid://shopify/Order/1042');
    expect(orderGidFromPayload('refunds/create', { id: 999 })).toBeNull();
  });

  test('orders/edited reads order_edit.order_id, never the edit id or a top-level id', () => {
    expect(orderGidFromPayload('orders/edited', { order_edit: { id: 78912, order_id: 1042 } })).toBe(
      'gid://shopify/Order/1042',
    );
    expect(orderGidFromPayload('orders/edited', { order_edit: { id: 78912 } })).toBeNull();
    expect(orderGidFromPayload('orders/edited', { id: 1042 })).toBeNull();
    expect(orderGidFromPayload('orders/edited', { order_edit: null })).toBeNull();
    expect(orderGidFromPayload('orders/edited', {})).toBeNull();
  });
});
