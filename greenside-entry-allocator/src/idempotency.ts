/**
 * The allocation identity.
 *
 * The key is a deterministic hash of (shop, order, line item), so every path
 * that could process the same work -- a webhook, a Shopify retry, the
 * reconciler, a manual replay -- computes the SAME key and converges on the
 * same ledger row instead of creating a second one.
 *
 * Line item rather than order, because one order can hold several
 * competitions and each needs its own range.
 *
 * 'v1' is a deliberate escape hatch: if allocation semantics ever change such
 * that re-allocation is wanted, bump the version rather than delete rows.
 */

export const ALLOCATION_KEY_VERSION = 'v1';

function toHex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer), (b) => b.toString(16).padStart(2, '0')).join('');
}

export async function allocationId(shopDomain: string, orderId: string, lineItemId: string): Promise<string> {
  const identity = `gsalloc:${ALLOCATION_KEY_VERSION}:${shopDomain}:${orderId}:${lineItemId}`;
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(identity));
  return toHex(digest);
}
