/**
 * Entry-number generation.
 *
 * Pure functions only. No clock, no network, no database. The same inputs
 * always render the same numbers, which is what makes a published entry list
 * reproducible by anyone holding the competition's configuration.
 *
 * A range is INCLUSIVE of both endpoints:
 *
 *   prefix 'PUT', start 1001, capacity 100  ->  PUT1001 .. PUT1100
 *
 * That is exactly 100 numbers, because 1100 - 1001 + 1 = 100. Getting this
 * off by one would issue 99 or 101 entries against a published cap of 100.
 *
 * Nothing here knows about PUT, or about any particular competition.
 */

export interface NumberingConfig {
  prefix: string;
  startNumber: number;
  capacity: number;
  padWidth: number;
}

export const MIN_PREFIX_LENGTH = 2;
export const MAX_PREFIX_LENGTH = 6;
export const MIN_PAD_WIDTH = 1;
export const MAX_PAD_WIDTH = 9;

/** Uppercase A-Z only, 2-6 characters. Matches the CHECK constraints in 0001. */
const PREFIX_PATTERN = /^[A-Z]{2,6}$/;

export class NumberingError extends Error {
  constructor(message: string, readonly code: string) {
    super(message);
    this.name = 'NumberingError';
  }
}

/**
 * Canonical form of a prefix: trimmed and uppercased.
 *
 * Normalising before storage is what makes the unique index meaningful --
 * 'put' and 'PUT' must not be able to coexist as two competitions.
 */
export function canonicalPrefix(raw: string): string {
  return raw.trim().toUpperCase();
}

/** The last sequence number in the range. Inclusive. */
export function lastSeq(config: NumberingConfig): number {
  return config.startNumber + config.capacity - 1;
}

/**
 * Validate a numbering configuration.
 *
 * Returns the list of problems rather than throwing on the first, so a
 * misconfigured competition reports everything wrong with it in one go.
 */
export function validateNumbering(config: NumberingConfig): string[] {
  const problems: string[] = [];

  if (!PREFIX_PATTERN.test(config.prefix)) {
    problems.push(
      `prefix must be ${MIN_PREFIX_LENGTH}-${MAX_PREFIX_LENGTH} uppercase letters A-Z, got ${JSON.stringify(config.prefix)}`,
    );
  }
  if (!Number.isInteger(config.startNumber) || config.startNumber < 0) {
    problems.push(`start_number must be an integer >= 0, got ${config.startNumber}`);
  }
  if (!Number.isInteger(config.capacity) || config.capacity < 1) {
    problems.push(`capacity must be an integer >= 1, got ${config.capacity}`);
  }
  if (!Number.isInteger(config.padWidth) || config.padWidth < MIN_PAD_WIDTH || config.padWidth > MAX_PAD_WIDTH) {
    problems.push(`pad_width must be an integer ${MIN_PAD_WIDTH}-${MAX_PAD_WIDTH}, got ${config.padWidth}`);
  }

  // Only meaningful once the numbers themselves are sane.
  if (problems.length === 0) {
    const highest = lastSeq(config);
    const needed = String(highest).length;
    if (needed > config.padWidth) {
      problems.push(
        `pad_width ${config.padWidth} is too small for the highest number ${highest}, which needs ${needed} digits`,
      );
    }
  }

  return problems;
}

/**
 * The default pad width for a configuration: the width of its highest number.
 *
 * Deriving it is a convenience for creating a competition, not a runtime rule.
 * Once stored, pad_width is authoritative -- otherwise growing a pool would
 * silently renumber every existing entry.
 */
export function defaultPadWidth(startNumber: number, capacity: number): number {
  return String(startNumber + capacity - 1).length;
}

/** Render one sequence number, e.g. (PUT, 1001, 4) -> 'PUT1001'. */
export function renderEntryNumber(prefix: string, seq: number, padWidth: number): string {
  return prefix + String(seq).padStart(padWidth, '0');
}

/**
 * Every sequence number in the range, ascending.
 *
 * Used to materialise a pool. Kept separate from rendering so a caller can
 * stream large ranges without building both arrays.
 */
export function* seqRange(config: NumberingConfig): Generator<number> {
  const end = lastSeq(config);
  for (let seq = config.startNumber; seq <= end; seq++) yield seq;
}

/** Every rendered entry number in the range, ascending. */
export function renderRange(config: NumberingConfig): string[] {
  const out: string[] = [];
  for (const seq of seqRange(config)) out.push(renderEntryNumber(config.prefix, seq, config.padWidth));
  return out;
}

/** True when `seq` falls inside the configured range. */
export function isInRange(config: NumberingConfig, seq: number): boolean {
  return seq >= config.startNumber && seq <= lastSeq(config);
}

/**
 * Whether two configurations would render any identical entry number.
 *
 * Same prefix and overlapping sequence ranges is the only way this happens,
 * and the database refuses it independently via ux_entry_number_global. This
 * exists so the refusal comes with a useful message instead of a constraint
 * error.
 */
export function rangesCollide(a: NumberingConfig, b: NumberingConfig): boolean {
  if (a.prefix !== b.prefix) return false;
  // Differing pad widths on the same prefix can still collide (PUT01 vs PUT1
  // do not, but PUT0001 and PUT1 both render seq 1 differently). Compare the
  // rendered endpoints rather than the raw sequence numbers.
  if (a.padWidth !== b.padWidth) {
    const aSet = new Set(renderRange(a));
    return renderRange(b).some((n) => aSet.has(n));
  }
  return a.startNumber <= lastSeq(b) && b.startNumber <= lastSeq(a);
}
