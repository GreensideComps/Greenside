/**
 * Claiming and releasing entry numbers.
 *
 * Idempotency here does NOT depend on the allocation ledger having been
 * written. CLAIM_LOWEST stamps allocation_id onto the pool rows in the same
 * statement that claims them, so the question "has this work already been
 * done?" is answered by the pool itself:
 *
 *   held == target      already done. Nothing to do.
 *   held == 0           allocate `target`.
 *   0 < held < target   a crash mid-flight. Allocate the difference; REPAIRED.
 *   held  > target      converge down. See converge.ts.
 *
 * That is what makes a duplicate webhook, a Shopify retry and a reconciler
 * pass all safe against the same order.
 */

import {
  CLAIM_LOWEST,
  COUNT_AVAILABLE,
  COUNT_HELD_BY_ALLOCATION,
  RELEASE_HIGHEST,
  SELECT_HELD_BY_ALLOCATION,
  type D1Like,
} from './db';
import type { ReleaseReason } from './events';

export interface ClaimedNumber {
  entry_number: string;
  seq: number;
  allocation_seq: number;
}

export type ClaimResult =
  | { ok: true; claimed: ClaimedNumber[] }
  | { ok: false; code: 'REFUSED_CAPACITY'; available: number; requested: number };

/**
 * Claim `count` numbers, lowest first, all or nothing.
 *
 * Zero rows returned means fewer than `count` were available, because the
 * statement's capacity sub-predicate is false for every row in that case. The
 * caller must treat that as a refusal, never as a partial success.
 */
export async function claimLowest(args: {
  db: D1Like;
  competitionId: string;
  allocationId: string;
  count: number;
  now: string;
  orderId: string;
  lineItemId: string;
  customerRef: string | null;
}): Promise<ClaimResult> {
  if (args.count <= 0) return { ok: true, claimed: [] };

  const claimed = await args.db
    .prepare(CLAIM_LOWEST)
    .bind(
      args.competitionId,
      args.allocationId,
      args.now,
      args.count,
      args.orderId,
      args.lineItemId,
      args.customerRef,
    )
    .all<ClaimedNumber>();

  if (claimed.results.length === 0) {
    const available = await args.db.prepare(COUNT_AVAILABLE).bind(args.competitionId).first<{ n: number }>();
    return { ok: false, code: 'REFUSED_CAPACITY', available: available?.n ?? 0, requested: args.count };
  }

  // Ascending, so the caller and the customer see 'PUT1001, PUT1002, PUT1003'.
  return { ok: true, claimed: claimed.results.slice().sort((a, b) => a.seq - b.seq) };
}

/**
 * Release the highest `count` numbers held by one allocation.
 *
 * `returnToPool` is decided by the caller from the competition's status, and
 * is the single place the freeze invariant is enforced: while OPEN a released
 * number goes straight back to AVAILABLE; once FROZEN it stays RELEASED for
 * good, so a post-freeze refund can never hand a drawn number to someone new.
 */
export async function releaseHighest(args: {
  db: D1Like;
  competitionId: string;
  allocationId: string;
  count: number;
  now: string;
  reason: ReleaseReason;
  returnToPool: boolean;
}): Promise<ClaimedNumber[]> {
  if (args.count <= 0) return [];

  const released = await args.db
    .prepare(RELEASE_HIGHEST)
    .bind(
      args.competitionId,
      args.allocationId,
      args.now,
      args.count,
      args.returnToPool ? 'AVAILABLE' : 'RELEASED',
      args.reason,
    )
    .all<ClaimedNumber>();

  // Descending: highest released first, which is the order they were taken.
  return released.results.slice().sort((a, b) => b.seq - a.seq);
}

export async function countHeld(db: D1Like, competitionId: string, allocationId: string): Promise<number> {
  const row = await db.prepare(COUNT_HELD_BY_ALLOCATION).bind(competitionId, allocationId).first<{ n: number }>();
  return row?.n ?? 0;
}

export async function listHeld(db: D1Like, competitionId: string, allocationId: string): Promise<ClaimedNumber[]> {
  const rows = await db.prepare(SELECT_HELD_BY_ALLOCATION).bind(competitionId, allocationId).all<ClaimedNumber>();
  return rows.results;
}
