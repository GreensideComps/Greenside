import { Logger } from '../src/logging';
import type { ProductNode } from '../src/shopify';
import type { CloserConfig } from '../src/eligibility';

export const LOCATION = 'gid://shopify/Location/119443915126';

export const CONFIG: CloserConfig = {
  allowedLocationId: LOCATION,
  requiredLiveTag: 'competition-live',
  closedTag: 'gs-closed-zeroed',
};

/** A logger that captures instead of printing, so assertions can read it. */
export function captureLogger(secrets: string[] = []): { logger: Logger; lines: string[] } {
  const lines: string[] = [];
  const logger = new Logger({}, secrets, (line) => lines.push(line));
  return { logger, lines };
}

export function product(overrides: Partial<ProductNode> = {}): ProductNode {
  return {
    id: 'gid://shopify/Product/1',
    title: 'QA-3 Test Competition',
    status: 'ACTIVE',
    tags: ['competition-live'],
    variantsCount: { count: 1 },
    closingAt: { value: '2026-09-01T20:00:00+01:00', type: 'date_time' },
    variants: {
      nodes: [
        {
          id: 'gid://shopify/ProductVariant/1',
          inventoryItem: {
            id: 'gid://shopify/InventoryItem/1',
            inventoryLevel: { id: 'lvl', quantities: [{ name: 'available', quantity: 500 }] },
          },
        },
      ],
    },
    ...overrides,
  };
}

/** Build a product with a specific available quantity. */
export function productWithQty(qty: number | null): ProductNode {
  const p = product();
  p.variants.nodes[0].inventoryItem!.inventoryLevel =
    qty === null ? null : { id: 'lvl', quantities: [{ name: 'available', quantity: qty }] };
  return p;
}

export const NOW = Date.parse('2026-09-22T09:00:00Z');
