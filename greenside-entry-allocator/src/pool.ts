/**
 * The entry-number pool.
 *
 * The pool is MATERIALISED: one row per number, created up front. That is what
 * makes "lowest available first" a query rather than arithmetic, and what lets
 * a released number be reused without ever computing
 * `next = sales + 1`, which would be wrong the moment anything is refunded.
 */

import { renderEntryNumber, seqRange, type NumberingConfig } from './numbering';
import {
  COUNT_NON_AVAILABLE_ABOVE,
  COUNT_POOL,
  COUNT_POOL_BY_STATUS,
  INSERT_POOL_ROW,
  MAX_POOL_SEQ,
  type D1Like,
} from './db';

export interface PoolCounts {
  total: number;
  available: number;
  allocated: number;
  released: number;
}

export type PoolBuildResult =
  | { ok: true; created: number; alreadyPresent: number }
  | { ok: false; code: 'PREFIX_TAKEN' | 'DUPLICATE_ENTRY_NUMBER' | 'SHRINK_REFUSED'; detail: string };

export async function poolCounts(db: D1Like, competitionId: string): Promise<PoolCounts> {
  const rows = await db.prepare(COUNT_POOL_BY_STATUS).bind(competitionId).all<{ status: string; n: number }>();
  const counts: PoolCounts = { total: 0, available: 0, allocated: 0, released: 0 };
  for (const row of rows.results) {
    counts.total += row.n;
    if (row.status === 'AVAILABLE') counts.available = row.n;
    if (row.status === 'ALLOCATED') counts.allocated = row.n;
    if (row.status === 'RELEASED') counts.released = row.n;
  }
  return counts;
}

/**
 * Build or extend a pool. Idempotent.
 *
 * Re-running with the same configuration inserts nothing and reports what was
 * already there. Re-running after `capacity` has been RAISED appends the new
 * numbers; existing numbers keep their sequence, their status and their
 * history, because renumbering a live competition would invalidate every
 * entry number already issued.
 *
 * A REDUCED capacity is refused outright if any number that would disappear is
 * not free. Silently deleting an allocated number is not a recoverable error.
 */
export async function buildPool(
  db: D1Like,
  competitionId: string,
  numbering: NumberingConfig,
): Promise<PoolBuildResult> {
  const existingMax = await db.prepare(MAX_POOL_SEQ).bind(competitionId).first<{ max_seq: number | null }>();
  const existingCount = await db.prepare(COUNT_POOL).bind(competitionId).first<{ n: number }>();
  const alreadyPresent = existingCount?.n ?? 0;
  const highestWanted = numbering.startNumber + numbering.capacity - 1;

  // Shrink check first: refusing must not leave a half-built pool behind.
  if (existingMax?.max_seq != null && existingMax.max_seq > highestWanted) {
    const blocked = await db
      .prepare(COUNT_NON_AVAILABLE_ABOVE)
      .bind(competitionId, highestWanted)
      .first<{ n: number }>();
    if ((blocked?.n ?? 0) > 0) {
      return {
        ok: false,
        code: 'SHRINK_REFUSED',
        detail:
          `capacity would drop the range to ${highestWanted}, but ${blocked?.n} number(s) above it are ` +
          `allocated or released; shrinking would destroy issued entry numbers`,
      };
    }
  }

  const statements = [];
  let created = 0;
  for (const seq of seqRange(numbering)) {
    // Append-only: numbers at or below the existing high-water mark already
    // exist and must not be touched.
    if (existingMax?.max_seq != null && seq <= existingMax.max_seq) continue;
    statements.push(
      db
        .prepare(INSERT_POOL_ROW)
        .bind(competitionId, seq, renderEntryNumber(numbering.prefix, seq, numbering.padWidth)),
    );
    created++;
  }

  if (statements.length > 0) {
    try {
      await db.batch(statements);
    } catch (err) {
      const message = String((err as Error)?.message ?? err);
      // ux_entry_number_global is the constraint that physically prevents two
      // competitions from ever issuing the same rendered string. Reaching it
      // means a prefix/range collision slipped past the application check.
      if (/ux_entry_number_global|UNIQUE constraint failed: entry_number.entry_number/.test(message)) {
        return {
          ok: false,
          code: 'DUPLICATE_ENTRY_NUMBER',
          detail: `a rendered entry number in this range already exists for another competition: ${message}`,
        };
      }
      throw err;
    }
  }

  return { ok: true, created, alreadyPresent };
}
