import { describe, it, expect } from 'vitest';
import { evaluateEligibility, parseClosingAt } from '../src/eligibility';
import { product, productWithQty, CONFIG, NOW } from './helpers';

describe('parseClosingAt', () => {
  it('parses an offset timestamp to the correct absolute instant', () => {
    // 20:00 BST is 19:00 UTC. If this ever asserts 20:00Z the code has
    // grown a local-time assumption.
    const r = parseClosingAt('2026-09-22T20:00:00+01:00');
    expect(r.ok).toBe(true);
    expect(r.iso).toBe('2026-09-22T19:00:00.000Z');
  });

  it('treats an offset value and its UTC equivalent as the same instant', () => {
    expect(parseClosingAt('2026-09-22T20:00:00+01:00').epochMs)
      .toBe(parseClosingAt('2026-09-22T19:00:00Z').epochMs);
  });

  it.each(['', '   ', 'banana', '2026', '2026-09', '2026-09-22', '01/02/2026'])(
    'rejects the non-timestamp value %j',
    (value) => {
      expect(parseClosingAt(value).ok).toBe(false);
    },
  );

  it('rejects null and undefined', () => {
    expect(parseClosingAt(null).ok).toBe(false);
    expect(parseClosingAt(undefined).ok).toBe(false);
  });

  it('rejects a well-formed but impossible date', () => {
    expect(parseClosingAt('2026-02-30T10:00:00Z').ok).toBe(false);
  });
});

describe('date eligibility', () => {
  it('is eligible when closing_at is in the past', () => {
    const p = product({ closingAt: { value: '2026-09-22T08:00:00Z', type: 'date_time' } });
    expect(evaluateEligibility(p, NOW, CONFIG).code).toBe('ELIGIBLE');
  });

  it('is eligible when closing_at is exactly now', () => {
    const p = product({ closingAt: { value: '2026-09-22T09:00:00Z', type: 'date_time' } });
    expect(evaluateEligibility(p, NOW, CONFIG).code).toBe('ELIGIBLE');
  });

  it('is NOT eligible one millisecond before closing', () => {
    const p = product({ closingAt: { value: '2026-09-22T09:00:00Z', type: 'date_time' } });
    expect(evaluateEligibility(p, NOW - 1, CONFIG).code).toBe('SKIP_NOT_YET_CLOSING');
  });

  it('is not eligible when closing_at is in the future', () => {
    const p = product({ closingAt: { value: '2027-01-01T00:00:00Z', type: 'date_time' } });
    expect(evaluateEligibility(p, NOW, CONFIG).code).toBe('SKIP_NOT_YET_CLOSING');
  });

  it('skips a missing closing_at', () => {
    expect(evaluateEligibility(product({ closingAt: null }), NOW, CONFIG).code)
      .toBe('SKIP_MISSING_CLOSING_AT');
  });

  it('skips an empty closing_at value', () => {
    const p = product({ closingAt: { value: '', type: 'date_time' } });
    expect(evaluateEligibility(p, NOW, CONFIG).code).toBe('SKIP_MISSING_CLOSING_AT');
  });

  it('skips an invalid closing_at value', () => {
    const p = product({ closingAt: { value: 'not-a-date', type: 'date_time' } });
    expect(evaluateEligibility(p, NOW, CONFIG).code).toBe('SKIP_INVALID_CLOSING_AT');
  });
});

describe('tag gates', () => {
  it('is eligible with the live tag present', () => {
    expect(evaluateEligibility(product(), NOW, CONFIG).code).toBe('ELIGIBLE');
  });

  it('skips when the live tag is absent', () => {
    const p = product({ tags: ['competition', 'drivers'] });
    expect(evaluateEligibility(p, NOW, CONFIG).code).toBe('SKIP_MISSING_LIVE_TAG');
  });

  it('skips when the closed tag is already present', () => {
    const p = product({ tags: ['competition-live', 'gs-closed-zeroed'] });
    expect(evaluateEligibility(p, NOW, CONFIG).code).toBe('SKIP_ALREADY_CLOSED_TAG');
  });

  it('matches tags case-insensitively, as Shopify does', () => {
    const p = product({ tags: ['Competition-Live'] });
    expect(evaluateEligibility(p, NOW, CONFIG).code).toBe('ELIGIBLE');
    const q = product({ tags: ['competition-live', 'GS-Closed-Zeroed'] });
    expect(evaluateEligibility(q, NOW, CONFIG).code).toBe('SKIP_ALREADY_CLOSED_TAG');
  });

  it('does not treat a tag that merely contains the live tag as a match', () => {
    const p = product({ tags: ['competition-live-draft'] });
    expect(evaluateEligibility(p, NOW, CONFIG).code).toBe('SKIP_MISSING_LIVE_TAG');
  });

  it('skips when tags are missing entirely', () => {
    const p = product({ tags: [] });
    expect(evaluateEligibility(p, NOW, CONFIG).code).toBe('SKIP_MISSING_LIVE_TAG');
  });
});

describe('status gate', () => {
  it.each(['DRAFT', 'ARCHIVED', 'UNLISTED', 'active', ''])(
    'skips status %j',
    (status) => {
      expect(evaluateEligibility(product({ status }), NOW, CONFIG).code).toBe('SKIP_NOT_ACTIVE');
    },
  );

  it('accepts ACTIVE', () => {
    expect(evaluateEligibility(product({ status: 'ACTIVE' }), NOW, CONFIG).code).toBe('ELIGIBLE');
  });

  it('skips a DRAFT product even when everything else qualifies', () => {
    // This is the Qi10 case: draft, tagged, past closing date.
    const p = product({
      status: 'DRAFT',
      tags: ['competition-live'],
      closingAt: { value: '2026-01-01T00:00:00Z', type: 'date_time' },
    });
    expect(evaluateEligibility(p, NOW, CONFIG).eligible).toBe(false);
  });
});

describe('inventory structure', () => {
  it('reads the available quantity at the allowed location', () => {
    const r = evaluateEligibility(productWithQty(500), NOW, CONFIG);
    expect(r.target?.available).toBe(500);
  });

  it('is eligible at inventory 1', () => {
    expect(evaluateEligibility(productWithQty(1), NOW, CONFIG).target?.available).toBe(1);
  });

  it('is eligible at inventory 0 so the tag step can still be completed', () => {
    const r = evaluateEligibility(productWithQty(0), NOW, CONFIG);
    expect(r.code).toBe('ELIGIBLE');
    expect(r.target?.available).toBe(0);
  });

  it('fails safely when the inventory level is missing', () => {
    expect(evaluateEligibility(productWithQty(null), NOW, CONFIG).code)
      .toBe('SKIP_MISSING_INVENTORY_LEVEL');
  });

  it('fails safely when the inventory item is missing', () => {
    const p = product();
    p.variants.nodes[0].inventoryItem = null;
    expect(evaluateEligibility(p, NOW, CONFIG).code).toBe('SKIP_MISSING_INVENTORY_ITEM');
  });

  it('fails safely when there is more than one variant', () => {
    const p = product({ variantsCount: { count: 2 } });
    expect(evaluateEligibility(p, NOW, CONFIG).code).toBe('UNEXPECTED_VARIANT_STRUCTURE');
  });

  it('fails safely when there are no variants', () => {
    const p = product({ variantsCount: { count: 0 }, variants: { nodes: [] } });
    expect(evaluateEligibility(p, NOW, CONFIG).code).toBe('UNEXPECTED_VARIANT_STRUCTURE');
  });

  it('ignores a quantity that is not named "available"', () => {
    const p = product();
    p.variants.nodes[0].inventoryItem!.inventoryLevel = {
      id: 'lvl',
      quantities: [{ name: 'on_hand', quantity: 500 }],
    };
    expect(evaluateEligibility(p, NOW, CONFIG).code).toBe('SKIP_MISSING_INVENTORY_LEVEL');
  });
});
