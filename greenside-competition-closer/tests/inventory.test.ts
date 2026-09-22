import { describe, it, expect } from 'vitest';
import { buildIdempotencyKey, referenceDocumentUri, closeCompetition, TARGET_QUANTITY } from '../src/inventory';
import type { ShopifyClient, SetQuantityResult } from '../src/shopify';
import { captureLogger, CONFIG, LOCATION } from './helpers';

const UUID_SHAPE = /^[0-9a-f]{8}-[0-9a-f]{4}-8[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const PRODUCT = { id: 'gid://shopify/Product/15892136296822', title: 'QA-3' };
const CLOSING = '2026-09-22T19:00:00.000Z';
const TARGET = {
  variantId: 'gid://shopify/ProductVariant/1',
  inventoryItemId: 'gid://shopify/InventoryItem/1',
  available: 500,
};

/** A scriptable stand-in for the Shopify client. */
function fakeClient(script: {
  setResults?: SetQuantityResult[];
  reads?: Array<number | null>;
  tagErrors?: Array<{ message: string }>;
  onSet?: (args: Record<string, unknown>) => void;
}): ShopifyClient & { calls: { set: number; read: number; tag: number; setArgs: any[] } } {
  const calls = { set: 0, read: 0, tag: 0, setArgs: [] as any[] };
  const setResults = script.setResults ?? [];
  const reads = script.reads ?? [];

  return {
    calls,
    async setAvailableQuantity(args: any) {
      calls.setArgs.push(args);
      script.onSet?.(args);
      const r = setResults[calls.set] ?? { adjustmentGroupId: 'g', quantityAfterChange: 0, userErrors: [] };
      calls.set += 1;
      return r;
    },
    async readAvailable() {
      // Distinguish "no scripted entry" from a scripted null; ?? would collapse them.
      const r = calls.read < reads.length ? reads[calls.read] : 0;
      calls.read += 1;
      return r;
    },
    async addTags() {
      calls.tag += 1;
      return script.tagErrors ?? [];
    },
  } as unknown as ShopifyClient & { calls: typeof calls };
}

const deps = (client: ShopifyClient, dryRun = false) => ({
  client,
  logger: captureLogger().logger,
  config: CONFIG,
  dryRun,
});

describe('buildIdempotencyKey', () => {
  it('produces a UUID-shaped key', async () => {
    expect(await buildIdempotencyKey(PRODUCT.id, CLOSING, 500)).toMatch(UUID_SHAPE);
  });

  it('is deterministic for the same logical operation', async () => {
    const a = await buildIdempotencyKey(PRODUCT.id, CLOSING, 500);
    const b = await buildIdempotencyKey(PRODUCT.id, CLOSING, 500);
    expect(a).toBe(b);
  });

  it('differs per product', async () => {
    const a = await buildIdempotencyKey('gid://shopify/Product/1', CLOSING, 500);
    const b = await buildIdempotencyKey('gid://shopify/Product/2', CLOSING, 500);
    expect(a).not.toBe(b);
  });

  it('differs when the observed quantity differs', async () => {
    // Required: a compare-and-set retry sends different arguments, and reusing
    // the key would be rejected with IDEMPOTENCY_KEY_PARAMETER_MISMATCH.
    const a = await buildIdempotencyKey(PRODUCT.id, CLOSING, 500);
    const b = await buildIdempotencyKey(PRODUCT.id, CLOSING, 499);
    expect(a).not.toBe(b);
  });

  it('differs when closing_at is changed', async () => {
    const a = await buildIdempotencyKey(PRODUCT.id, CLOSING, 500);
    const b = await buildIdempotencyKey(PRODUCT.id, '2026-10-01T19:00:00.000Z', 500);
    expect(a).not.toBe(b);
  });
});

describe('referenceDocumentUri', () => {
  it('is a valid gid URI naming this service', () => {
    expect(referenceDocumentUri(PRODUCT.id))
      .toBe('gid://greenside-competition-closer/CompetitionClose/15892136296822');
  });
});

describe('closeCompetition', () => {
  it('performs no writes in dry-run mode', async () => {
    const client = fakeClient({});
    const out = await closeCompetition(deps(client, true), PRODUCT, TARGET, CLOSING);
    expect(out.finalState).toBe('DRY_RUN_WOULD_CLOSE');
    expect(client.calls.set).toBe(0);
    expect(client.calls.tag).toBe(0);
    expect(client.calls.read).toBe(0);
  });

  it('logs WOULD_CLOSE with the real numbers in dry-run mode', async () => {
    const { logger, lines } = captureLogger();
    await closeCompetition({ client: fakeClient({}), logger, config: CONFIG, dryRun: true },
      PRODUCT, TARGET, CLOSING);
    const entry = JSON.parse(lines[0]);
    expect(entry.event).toBe('WOULD_CLOSE');
    expect(entry.current_inventory).toBe(500);
    expect(entry.target_inventory).toBe(0);
    expect(entry.would_add_tag).toBe('gs-closed-zeroed');
  });

  it('sets inventory to zero, verifies, then tags', async () => {
    const client = fakeClient({ reads: [0] });
    const out = await closeCompetition(deps(client), PRODUCT, TARGET, CLOSING);
    expect(out.finalState).toBe('CLOSED');
    expect(out.inventoryMutationResult).toBe('SET_TO_ZERO');
    expect(out.verificationResult).toBe('AVAILABLE_IS_ZERO');
    expect(out.tagResult).toBe('ADDED');
    expect(client.calls.set).toBe(1);
    expect(client.calls.tag).toBe(1);
  });

  it('always targets exactly zero at the allowed location, using compare-and-set', async () => {
    const client = fakeClient({ reads: [0] });
    await closeCompetition(deps(client), PRODUCT, TARGET, CLOSING);
    const args = client.calls.setArgs[0];
    expect(args.quantity).toBe(TARGET_QUANTITY);
    expect(args.quantity).toBe(0);
    expect(args.changeFromQuantity).toBe(500);
    expect(args.locationId).toBe(LOCATION);
    expect(args.idempotencyKey).toMatch(UUID_SHAPE);
  });

  it('does not mutate again when inventory is already zero, but does tag', async () => {
    const client = fakeClient({ reads: [0] });
    const out = await closeCompetition(deps(client), PRODUCT, { ...TARGET, available: 0 }, CLOSING);
    expect(client.calls.set).toBe(0);
    expect(out.inventoryMutationResult).toBe('SKIPPED_ALREADY_ZERO');
    expect(out.finalState).toBe('ALREADY_CLOSED');
    expect(client.calls.tag).toBe(1);
  });

  it('retries a compare-and-set conflict against the re-read quantity', async () => {
    const client = fakeClient({
      setResults: [
        { adjustmentGroupId: null, quantityAfterChange: null,
          userErrors: [{ code: 'CHANGE_FROM_QUANTITY_STALE', message: 'stale' }] },
        { adjustmentGroupId: 'g', quantityAfterChange: 0, userErrors: [] },
      ],
      reads: [480, 0],
    });
    const out = await closeCompetition(deps(client), PRODUCT, TARGET, CLOSING);
    expect(out.finalState).toBe('CLOSED');
    expect(client.calls.set).toBe(2);
    expect(client.calls.setArgs[0].changeFromQuantity).toBe(500);
    expect(client.calls.setArgs[1].changeFromQuantity).toBe(480);
    // A new observed quantity must produce a new idempotency key.
    expect(client.calls.setArgs[0].idempotencyKey).not.toBe(client.calls.setArgs[1].idempotencyKey);
  });

  it('gives up after three attempts and does not tag', async () => {
    const stale = {
      adjustmentGroupId: null, quantityAfterChange: null,
      userErrors: [{ code: 'CHANGE_FROM_QUANTITY_STALE', message: 'stale' }],
    };
    const client = fakeClient({ setResults: [stale, stale, stale], reads: [400, 300, 0] });
    const out = await closeCompetition(deps(client), PRODUCT, TARGET, CLOSING);
    expect(client.calls.set).toBe(3);
    expect(out.finalState).toBe('CLOSE_FAILED');
    expect(client.calls.tag).toBe(0);
  });

  it('stops immediately on a permanent user error', async () => {
    const client = fakeClient({
      setResults: [{ adjustmentGroupId: null, quantityAfterChange: null,
        userErrors: [{ code: 'ITEM_NOT_STOCKED_AT_LOCATION', message: 'nope' }] }],
    });
    const out = await closeCompetition(deps(client), PRODUCT, TARGET, CLOSING);
    expect(client.calls.set).toBe(1);
    expect(out.finalState).toBe('CLOSE_FAILED');
    expect(out.errorCode).toBe('ITEM_NOT_STOCKED_AT_LOCATION');
    expect(client.calls.tag).toBe(0);
  });

  it('treats a concurrent run reaching zero first as success', async () => {
    const client = fakeClient({
      setResults: [{ adjustmentGroupId: null, quantityAfterChange: null,
        userErrors: [{ code: 'CHANGE_FROM_QUANTITY_STALE', message: 'stale' }] }],
      reads: [0, 0],
    });
    const out = await closeCompetition(deps(client), PRODUCT, TARGET, CLOSING);
    expect(out.inventoryMutationResult).toBe('ALREADY_ZERO_AFTER_CONFLICT');
    expect(out.finalState).toBe('CLOSED');
    expect(client.calls.tag).toBe(1);
  });

  it('never tags when verification does not read back zero', async () => {
    const client = fakeClient({ reads: [7] });
    const out = await closeCompetition(deps(client), PRODUCT, TARGET, CLOSING);
    expect(out.finalState).toBe('VERIFICATION_FAILED');
    expect(out.verificationResult).toBe('EXPECTED_0_GOT_7');
    expect(client.calls.tag).toBe(0);
  });

  it('never tags when verification cannot read the level at all', async () => {
    const client = fakeClient({ reads: [null] });
    const out = await closeCompetition(deps(client), PRODUCT, TARGET, CLOSING);
    expect(out.finalState).toBe('VERIFICATION_FAILED');
    expect(client.calls.tag).toBe(0);
  });

  it('reports TAG_FAILED without re-zeroing when tagging fails', async () => {
    const client = fakeClient({ reads: [0], tagErrors: [{ message: 'denied' }] });
    const out = await closeCompetition(deps(client), PRODUCT, TARGET, CLOSING);
    expect(out.finalState).toBe('TAG_FAILED');
    expect(out.verificationResult).toBe('AVAILABLE_IS_ZERO');
    expect(client.calls.set).toBe(1);
  });

  it('recovers a partial failure: inventory zero, tag missing', async () => {
    // The next scheduled run sees available=0 and no closed tag.
    const client = fakeClient({ reads: [0] });
    const out = await closeCompetition(deps(client), PRODUCT, { ...TARGET, available: 0 }, CLOSING);
    expect(client.calls.set).toBe(0);
    expect(client.calls.tag).toBe(1);
    expect(out.finalState).toBe('ALREADY_CLOSED');
  });
});
