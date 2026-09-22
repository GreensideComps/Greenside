/**
 * Whether a competition may be closed automatically.
 *
 * Pure functions only -- no network, no clock of its own. The current time is
 * always passed in, so every rule here is directly testable and the same
 * inputs always give the same verdict.
 *
 * The default answer is NO. A product is closed only when every check below
 * passes; anything unrecognised, missing or ambiguous is a skip.
 */

import type { ProductNode, ProductVariantNode } from './shopify';
import { QUANTITY_NAME } from './shopify';

export type EligibilityCode =
  | 'ELIGIBLE'
  | 'SKIP_NOT_ACTIVE'
  | 'SKIP_MISSING_LIVE_TAG'
  | 'SKIP_ALREADY_CLOSED_TAG'
  | 'SKIP_MISSING_CLOSING_AT'
  | 'SKIP_INVALID_CLOSING_AT'
  | 'SKIP_NOT_YET_CLOSING'
  | 'UNEXPECTED_VARIANT_STRUCTURE'
  | 'SKIP_MISSING_INVENTORY_ITEM'
  | 'SKIP_MISSING_INVENTORY_LEVEL';

export interface CloserConfig {
  /** Only products carrying this tag may ever be closed. */
  requiredLiveTag: string;
  /** Added after a verified zero; its presence blocks reprocessing. */
  closedTag: string;
  /** The one location whose available quantity may be changed. */
  allowedLocationId: string;
}

export interface EligibleTarget {
  variantId: string;
  inventoryItemId: string;
  available: number;
}

export interface EligibilityResult {
  code: EligibilityCode;
  eligible: boolean;
  closingAtIso: string | null;
  closingAtEpochMs: number | null;
  target: EligibleTarget | null;
  detail?: string;
}

/**
 * ISO 8601 with, at minimum, a date and a wall-clock time.
 *
 * Date.parse alone is too permissive: it accepts "2026" and other fragments
 * that would silently become midnight on 1 January, closing a competition
 * months early. A metafield of type date_time always carries a full
 * timestamp, so anything less is a corrupt value and must be skipped.
 */
const ISO_DATETIME =
  /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$/;

function daysInMonth(year: number, month: number): number {
  const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
  return [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1] ?? 0;
}

/**
 * Reject a timestamp that is shaped correctly but names a date that does not
 * exist. Date.parse rolls "2026-02-30" forward to 2 March rather than
 * failing, which would close a competition on a day nobody chose. The
 * calendar is checked explicitly instead.
 */
function isRealCalendarDateTime(value: string): boolean {
  const m = ISO_DATETIME.exec(value);
  if (!m) return false;
  const [, y, mo, d, h, mi, sec] = m;
  const year = Number(y);
  const month = Number(mo);
  const day = Number(d);
  const hour = Number(h);
  const minute = Number(mi);
  const second = sec === undefined ? 0 : Number(sec);

  if (month < 1 || month > 12) return false;
  if (day < 1 || day > daysInMonth(year, month)) return false;
  if (hour > 23 || minute > 59) return false;
  // 60 is a leap second, which Shopify will not emit and Date.parse mangles.
  if (second > 59) return false;
  return true;
}

export interface ParsedClosingAt {
  ok: boolean;
  epochMs: number | null;
  iso: string | null;
}

/**
 * Parse closing_at to an absolute instant.
 *
 * The result is epoch milliseconds, which is UTC by definition. Every
 * comparison downstream is therefore offset-safe: a value written as
 * "+01:00" during BST and one written as "Z" in winter compare correctly
 * against each other and against the Worker's clock, with no local-time
 * arithmetic anywhere in this codebase.
 */
export function parseClosingAt(raw: string | null | undefined): ParsedClosingAt {
  if (typeof raw !== 'string' || raw.trim() === '') return { ok: false, epochMs: null, iso: null };
  const value = raw.trim();
  if (!isRealCalendarDateTime(value)) return { ok: false, epochMs: null, iso: null };

  const epochMs = Date.parse(value);
  if (Number.isNaN(epochMs)) return { ok: false, epochMs: null, iso: null };

  return { ok: true, epochMs, iso: new Date(epochMs).toISOString() };
}

/** Shopify treats tags case-insensitively, so comparison does too. */
function hasTag(tags: string[] | null | undefined, tag: string): boolean {
  if (!Array.isArray(tags)) return false;
  const wanted = tag.trim().toLowerCase();
  return tags.some((t) => typeof t === 'string' && t.trim().toLowerCase() === wanted);
}

function availableAt(variant: ProductVariantNode, locationId: string): number | null {
  const level = variant.inventoryItem?.inventoryLevel;
  if (!level) return null;
  // The level was requested for exactly one location, so anything returned
  // belongs to that location; we still only accept the named quantity.
  const q = level.quantities?.find((x) => x.name === QUANTITY_NAME);
  return typeof q?.quantity === 'number' ? q.quantity : null;
}

function skip(code: EligibilityCode, closing: ParsedClosingAt, detail?: string): EligibilityResult {
  return {
    code,
    eligible: false,
    closingAtIso: closing.iso,
    closingAtEpochMs: closing.epochMs,
    target: null,
    detail,
  };
}

/**
 * The single gate every product passes through before any mutation.
 *
 * Order matters only for which reason gets logged; all conditions must hold.
 * `nowMs` is the caller's UTC clock reading for the whole run, so every
 * product in one run is judged against one consistent instant.
 */
export function evaluateEligibility(
  product: ProductNode,
  nowMs: number,
  config: CloserConfig,
): EligibilityResult {
  const closing = parseClosingAt(product.closingAt?.value ?? null);

  if (product.status !== 'ACTIVE') {
    return skip('SKIP_NOT_ACTIVE', closing, `status=${product.status}`);
  }
  if (!hasTag(product.tags, config.requiredLiveTag)) {
    return skip('SKIP_MISSING_LIVE_TAG', closing);
  }
  if (hasTag(product.tags, config.closedTag)) {
    return skip('SKIP_ALREADY_CLOSED_TAG', closing);
  }

  const raw = product.closingAt?.value;
  if (typeof raw !== 'string' || raw.trim() === '') {
    return skip('SKIP_MISSING_CLOSING_AT', closing);
  }
  if (!closing.ok || closing.epochMs === null) {
    return skip('SKIP_INVALID_CLOSING_AT', closing);
  }
  // "Reached" includes exactly now.
  if (closing.epochMs > nowMs) {
    return skip('SKIP_NOT_YET_CLOSING', closing);
  }

  // A competition is one variant. Anything else is a product we do not
  // understand, and we refuse to guess which variant the entries belong to.
  const variantCount = product.variantsCount?.count ?? product.variants?.nodes?.length ?? 0;
  const variants = product.variants?.nodes ?? [];
  if (variantCount !== 1 || variants.length !== 1) {
    return skip('UNEXPECTED_VARIANT_STRUCTURE', closing, `variants=${variantCount}`);
  }

  const variant = variants[0];
  const inventoryItemId = variant.inventoryItem?.id ?? null;
  if (!inventoryItemId) {
    return skip('SKIP_MISSING_INVENTORY_ITEM', closing);
  }

  const available = availableAt(variant, config.allowedLocationId);
  if (available === null) {
    // No level at the one permitted location: either the item is not stocked
    // there, or the product is stocked somewhere this Worker must not touch.
    return skip('SKIP_MISSING_INVENTORY_LEVEL', closing);
  }

  return {
    code: 'ELIGIBLE',
    eligible: true,
    closingAtIso: closing.iso,
    closingAtEpochMs: closing.epochMs,
    target: { variantId: variant.id, inventoryItemId, available },
  };
}
