import { describe, it, expect } from 'vitest';
import { runCloser, buildSearchQuery, isDryRun, resolveConfig, DEFAULTS, type Env } from '../src/index';
import { Logger } from '../src/logging';
import { ShopifyError, type ShopifyClient, type ProductNode } from '../src/shopify';
import { captureLogger, CONFIG, product, NOW } from './helpers';

function fakeClient(products: ProductNode[], opts: {
  discoveryError?: ShopifyError;
  tagErrors?: Array<{ message: string }>;
} = {}): ShopifyClient & { calls: { set: number; tag: number; search: string | null } } {
  const calls = { set: 0, tag: 0, search: null as string | null };
  return {
    calls,
    async fetchCandidates(search: string) {
      calls.search = search;
      if (opts.discoveryError) throw opts.discoveryError;
      return products;
    },
    async setAvailableQuantity() {
      calls.set += 1;
      return { adjustmentGroupId: 'g', quantityAfterChange: 0, userErrors: [] };
    },
    async readAvailable() {
      return 0;
    },
    async addTags() {
      calls.tag += 1;
      return opts.tagErrors ?? [];
    },
  } as unknown as ShopifyClient & { calls: typeof calls };
}

const LIVE_ENV: Env = { DRY_RUN: 'false' };
const run = (env: Env, client: ShopifyClient, logger = captureLogger().logger) =>
  runCloser(env, { client, logger, now: () => NOW });

describe('dry-run gate', () => {
  it('defaults to dry run when DRY_RUN is unset', () => {
    expect(isDryRun({})).toBe(true);
  });

  it.each(['true', 'TRUE', 'False', '0', '', 'no', 'yes', 'FALSE'])(
    'stays in dry run for DRY_RUN=%j',
    (value) => {
      expect(isDryRun({ DRY_RUN: value })).toBe(true);
    },
  );

  it('only the exact string "false" permits writes', () => {
    expect(isDryRun({ DRY_RUN: 'false' })).toBe(false);
  });

  it('performs no writes while in dry run, even for a fully eligible product', async () => {
    const client = fakeClient([product()]);
    const summary = await run({}, client);
    expect(summary.dryRun).toBe(true);
    expect(client.calls.set).toBe(0);
    expect(client.calls.tag).toBe(0);
    expect(summary.states.DRY_RUN_WOULD_CLOSE).toBe(1);
  });
});

describe('discovery query', () => {
  it('gates on status, the live tag, and the absence of the closed tag', () => {
    expect(buildSearchQuery(CONFIG))
      .toBe('status:active AND tag:competition-live AND -tag:gs-closed-zeroed');
  });

  it('never filters on the closing_at metafield', () => {
    // Shopify silently ignores an unsupported metafield predicate and returns
    // the unfiltered set, so relying on it would close open competitions.
    expect(buildSearchQuery(CONFIG)).not.toContain('closing_at');
    expect(buildSearchQuery(CONFIG)).not.toContain('metafield');
  });

  it('uses the configured tags', () => {
    expect(buildSearchQuery({ ...CONFIG, requiredLiveTag: 'x', closedTag: 'y' }))
      .toBe('status:active AND tag:x AND -tag:y');
  });
});

describe('configuration defaults', () => {
  it('falls back to the hard-coded safety values', () => {
    expect(resolveConfig({})).toEqual({
      allowedLocationId: DEFAULTS.allowedLocationId,
      requiredLiveTag: DEFAULTS.requiredLiveTag,
      closedTag: DEFAULTS.closedTag,
    });
  });

  it('pins the location to the online inventory location by default', () => {
    expect(resolveConfig({}).allowedLocationId).toBe('gid://shopify/Location/119443915126');
  });
});

describe('runCloser', () => {
  it('closes an eligible product end to end', async () => {
    const client = fakeClient([product()]);
    const summary = await run(LIVE_ENV, client);
    expect(summary.discovered).toBe(1);
    expect(summary.states.CLOSED).toBe(1);
    expect(client.calls.set).toBe(1);
    expect(client.calls.tag).toBe(1);
    expect(summary.errors).toBe(0);
  });

  it('skips a draft product even if discovery somehow returned it', async () => {
    // Defence in depth: the search already filters status:active.
    const client = fakeClient([product({ status: 'DRAFT' })]);
    const summary = await run(LIVE_ENV, client);
    expect(summary.states.SKIPPED).toBe(1);
    expect(client.calls.set).toBe(0);
  });

  it('skips a product lacking the live tag', async () => {
    const client = fakeClient([product({ tags: ['qa-test', 'delete-me'] })]);
    const summary = await run(LIVE_ENV, client);
    expect(summary.states.SKIPPED).toBe(1);
    expect(client.calls.set).toBe(0);
  });

  it('skips a product whose closing time has not arrived', async () => {
    const client = fakeClient([product({ closingAt: { value: '2099-01-01T00:00:00Z', type: 'date_time' } })]);
    const summary = await run(LIVE_ENV, client);
    expect(summary.states.SKIPPED).toBe(1);
    expect(client.calls.set).toBe(0);
  });

  it('processes a mixed batch, touching only the eligible one', async () => {
    const client = fakeClient([
      product({ id: 'gid://shopify/Product/qi10', status: 'DRAFT' }),
      product({ id: 'gid://shopify/Product/qa1', tags: ['qa-test'] }),
      product({ id: 'gid://shopify/Product/qa3' }),
      product({ id: 'gid://shopify/Product/future', closingAt: { value: '2099-01-01T00:00:00Z', type: 'date_time' } }),
    ]);
    const summary = await run(LIVE_ENV, client);
    expect(summary.discovered).toBe(4);
    expect(summary.states.SKIPPED).toBe(3);
    expect(summary.states.CLOSED).toBe(1);
    expect(client.calls.set).toBe(1);
  });

  it('reports a failed discovery without throwing', async () => {
    const client = fakeClient([], { discoveryError: new ShopifyError('boom', 'HTTP', false, 500) });
    const summary = await run(LIVE_ENV, client);
    expect(summary.discovered).toBe(0);
    expect(summary.errors).toBe(1);
    expect(client.calls.set).toBe(0);
  });

  it('records TAG_FAILED and keeps going', async () => {
    const client = fakeClient([product()], { tagErrors: [{ message: 'denied' }] });
    const summary = await run(LIVE_ENV, client);
    expect(summary.states.TAG_FAILED).toBe(1);
    expect(summary.errors).toBe(1);
  });

  it('does not abandon the run when one product throws', async () => {
    let first = true;
    const client = fakeClient([
      product({ id: 'gid://shopify/Product/a' }),
      product({ id: 'gid://shopify/Product/b' }),
    ]);
    const original = client.setAvailableQuantity.bind(client);
    (client as any).setAvailableQuantity = async (args: any) => {
      if (first) { first = false; throw new ShopifyError('network', 'NETWORK', true); }
      return original(args);
    };
    const summary = await run(LIVE_ENV, client);
    expect(summary.states.CLOSE_FAILED).toBe(1);
    expect(summary.states.CLOSED).toBe(1);
  });

  it('judges every product in a run against one clock reading', async () => {
    const boundary = '2026-09-22T09:00:00Z';
    const client = fakeClient([
      product({ id: 'gid://shopify/Product/a', closingAt: { value: boundary, type: 'date_time' } }),
      product({ id: 'gid://shopify/Product/b', closingAt: { value: boundary, type: 'date_time' } }),
    ]);
    const summary = await run(LIVE_ENV, client);
    expect(summary.states.CLOSED).toBe(2);
  });
});

describe('logging', () => {
  it('emits one JSON object per line with the required fields', async () => {
    const { logger, lines } = captureLogger();
    await run(LIVE_ENV, fakeClient([product()]), logger);
    const processed = lines.map((l) => JSON.parse(l)).find((e) => e.event === 'product_processed');
    expect(processed).toBeDefined();
    for (const field of [
      'timestamp', 'product_id', 'product_title', 'closing_at', 'status',
      'eligibility_result', 'current_inventory', 'target_inventory',
      'inventory_mutation_result', 'verification_result', 'tag_result', 'final_state',
    ]) {
      expect(processed).toHaveProperty(field);
    }
  });

  it('never prints a secret that reaches a log field', () => {
    const token = 'NOT_A_REAL_TOKEN_fixture_for_scrubbing_only';
    const lines: string[] = [];
    const logger = new Logger({}, [token], (l) => lines.push(l));
    logger.info('leaky', { note: `bearer ${token}`, access_token: token });
    expect(lines[0]).not.toContain(token);
    expect(lines[0]).toContain('[REDACTED]');
  });

  it('redacts secret-shaped field names regardless of value', () => {
    const lines: string[] = [];
    const logger = new Logger({}, [], (l) => lines.push(l));
    logger.info('e', { shopify_access_token: 'x', nested: { authorization: 'y' }, safe: 'keep' });
    const entry = JSON.parse(lines[0]);
    expect(entry.shopify_access_token).toBe('[REDACTED]');
    expect(entry.nested.authorization).toBe('[REDACTED]');
    expect(entry.safe).toBe('keep');
  });
});
