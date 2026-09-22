/**
 * Dry-run integration test against REAL Shopify data.
 *
 * The product payload in tests/fixtures/live-discovery-2026-09-22.json is the
 * verbatim response from the live store for the Worker's own discovery query.
 * The orchestration under test is the real runCloser, unmodified.
 *
 * Every write method on the injected client THROWS. A dry run that attempts
 * any mutation therefore fails loudly rather than passing quietly, and the
 * final scenario proves those detectors actually fire -- without it, "no
 * writes happened" would be an unfalsifiable claim.
 */
import { describe, it, expect } from 'vitest';
import { runCloser, type Env } from '../src/index';
import { Logger } from '../src/logging';
import type { ShopifyClient } from '../src/shopify';
import live from './fixtures/live-discovery-2026-09-22.json';

const LOCATION = 'gid://shopify/Location/119443915126';
const QA3_ID = 'gid://shopify/Product/15893655716214';
// A real wall-clock instant after QA-3's closing_at of 2026-09-01T12:00:00Z.
const NOW = Date.parse('2026-09-22T09:35:00Z');

class WriteAttempted extends Error {}

function liveClient(): { client: ShopifyClient; writes: string[] } {
  const writes: string[] = [];
  const client = {
    async fetchCandidates(search: string, locationId: string) {
      expect(search).toBe('status:active AND tag:competition-live AND -tag:gs-closed-zeroed');
      expect(locationId).toBe(LOCATION);
      return live.products.nodes as never;
    },
    async setAvailableQuantity() {
      writes.push('inventorySetQuantities');
      throw new WriteAttempted('inventorySetQuantities was called during a dry run');
    },
    async addTags() {
      writes.push('tagsAdd');
      throw new WriteAttempted('tagsAdd was called during a dry run');
    },
    async readAvailable() {
      writes.push('readAvailable');
      throw new WriteAttempted('readAvailable was called during a dry run');
    },
  } as unknown as ShopifyClient;
  return { client, writes };
}

function run(env: Env) {
  const lines: string[] = [];
  const logger = new Logger({ run_id: 'integration-dry-run' }, [], (l) => lines.push(l));
  const { client, writes } = liveClient();
  return { promise: runCloser(env, { client, logger, now: () => NOW }), lines, writes };
}

describe('dry-run integration against live QA-3 data', () => {
  it('discovers QA-3 and classifies it WOULD_CLOSE without any write', async () => {
    const { promise, lines, writes } = run({ DRY_RUN: 'true' });
    const summary = await promise;

    expect(summary.dryRun).toBe(true);
    expect(summary.discovered).toBe(1);
    expect(summary.states.DRY_RUN_WOULD_CLOSE).toBe(1);
    expect(summary.errors).toBe(0);

    // Not one write method was even reached.
    expect(writes).toEqual([]);

    const would = lines.map((l) => JSON.parse(l)).find((e) => e.event === 'WOULD_CLOSE');
    expect(would).toBeDefined();
    expect(would.product_id).toBe(QA3_ID);
    expect(would.current_inventory).toBe(100);
    expect(would.target_inventory).toBe(0);
    expect(would.would_add_tag).toBe('gs-closed-zeroed');

    console.log('\n===== DRY RUN LOG (DRY_RUN="true") =====');
    for (const l of lines) console.log(l);
    console.log('========================================\n');
  });

  it('is equally read-only when DRY_RUN is unset', async () => {
    const { promise, writes } = run({});
    const summary = await promise;
    expect(summary.dryRun).toBe(true);
    expect(summary.states.DRY_RUN_WOULD_CLOSE).toBe(1);
    expect(writes).toEqual([]);
  });

  it.each(['TRUE', 'True', '1', '', 'no', 'FALSE'])(
    'is read-only for the misconfigured value DRY_RUN=%j',
    async (value) => {
      const { promise, writes } = run({ DRY_RUN: value });
      await promise;
      expect(writes).toEqual([]);
    },
  );

  it('PROVES the write detectors fire: DRY_RUN="false" does attempt a write', async () => {
    // Without this, "no writes occurred" would be unfalsifiable.
    const { promise, writes } = run({ DRY_RUN: 'false' });
    const summary = await promise;
    expect(writes).toContain('inventorySetQuantities');
    expect(summary.states.DRY_RUN_WOULD_CLOSE).toBeUndefined();
    expect(summary.errors).toBeGreaterThan(0);
  });
});
