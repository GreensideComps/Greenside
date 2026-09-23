/**
 * Shopify webhook authentication.
 *
 * THE BODY IS VERIFIED AS RAW BYTES, BEFORE ANY JSON PARSE.
 *
 * Parsing and re-serialising changes the bytes -- key order, whitespace,
 * unicode escapes -- and the signature would no longer match. Every caller
 * must read the body once as text, verify it, and only then parse that exact
 * string.
 *
 * There is deliberately NO timestamp freshness window. Shopify retries a
 * failed delivery for up to 48 hours, and rejecting anything older than a few
 * minutes would discard exactly the legitimate retries the design relies on.
 * Replay protection is the allocation key's job, not this module's.
 */

/** Constant-time comparison. Length is allowed to leak; content is not. */
export function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/** Base64 HMAC-SHA256 of the raw body, as Shopify sends it. */
export async function computeHmac(rawBody: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const signature = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(rawBody));
  return toBase64(signature);
}

export type WebhookRejection =
  | 'MISSING_HMAC'
  | 'MISSING_SHOP_DOMAIN'
  | 'BAD_HMAC'
  | 'WRONG_SHOP'
  | 'MISSING_TOPIC'
  | 'TOPIC_MISMATCH';

export interface WebhookVerification {
  ok: boolean;
  rejection?: WebhookRejection;
  topic?: string;
  shopDomain?: string;
  webhookId?: string | null;
}

/**
 * Verify one webhook request.
 *
 * Order matters: signature first, then the shop, then the topic. A request
 * that fails the signature is never inspected further.
 */
export async function verifyWebhook(args: {
  rawBody: string;
  headers: Headers;
  secret: string;
  allowedShopDomain: string;
  expectedTopic?: string;
}): Promise<WebhookVerification> {
  const provided = args.headers.get('X-Shopify-Hmac-Sha256');
  if (!provided) return { ok: false, rejection: 'MISSING_HMAC' };

  const expected = await computeHmac(args.rawBody, args.secret);
  if (!timingSafeEqual(provided, expected)) return { ok: false, rejection: 'BAD_HMAC' };

  const shopDomain = args.headers.get('X-Shopify-Shop-Domain');
  if (!shopDomain) return { ok: false, rejection: 'MISSING_SHOP_DOMAIN' };
  if (shopDomain.toLowerCase() !== args.allowedShopDomain.toLowerCase()) {
    return { ok: false, rejection: 'WRONG_SHOP', shopDomain };
  }

  const topic = args.headers.get('X-Shopify-Topic');
  if (!topic) return { ok: false, rejection: 'MISSING_TOPIC', shopDomain };
  if (args.expectedTopic && topic !== args.expectedTopic) {
    return { ok: false, rejection: 'TOPIC_MISMATCH', topic, shopDomain };
  }

  return {
    ok: true,
    topic,
    shopDomain,
    // Recorded for visibility into Shopify's retry behaviour. NOT trusted as
    // the idempotency mechanism: the reconciler has no webhook id at all.
    webhookId: args.headers.get('X-Shopify-Webhook-Id'),
  };
}
