/**
 * OPEN -> FROZEN. Owned by the allocator.
 *
 * Freezing is the moment the entry list becomes final. After it:
 *   - no new allocation is accepted,
 *   - no released number returns to AVAILABLE,
 *   - the snapshot is stable.
 *
 * The second rule is the important one. Without it, a refund after the draw
 * could hand the winning number to a different customer, and "who owned
 * PUT1001" would have no defensible answer.
 *
 * Freeze REFUSES rather than warns. A competition that reaches a draw with
 * unresolved state produces a result nobody can defend, and there is no way to
 * repair that after the fact.
 */

import { COUNT_UNJUDGED, FREEZE_COMPETITION, SELECT_ORPHANED_CLAIMS, type D1Like } from './db';
import { poolCounts } from './pool';

export interface FreezeBlocker {
  code:
    | 'NOT_OPEN'
    | 'CONFIG_INVALID'
    | 'UNJUDGED_ALLOCATIONS'
    | 'ORPHANED_CLAIMS'
    | 'POOL_SIZE_MISMATCH'
    | 'ALLOCATION_LEDGER_DRIFT';
  detail: string;
}

export type FreezeResult =
  | { ok: true; frozenAt: string; eligible: number }
  | { ok: false; blockers: FreezeBlocker[] };

/**
 * Check every freeze precondition. Read-only: safe to call at any time to ask
 * "could this competition be frozen right now?".
 */
export async function freezeBlockers(args: {
  db: D1Like;
  competitionId: string;
  competitionStatus: string;
  expectedCapacity: number;
  configValid: boolean;
  configDetail?: string;
}): Promise<FreezeBlocker[]> {
  const blockers: FreezeBlocker[] = [];

  if (args.competitionStatus !== 'OPEN') {
    blockers.push({ code: 'NOT_OPEN', detail: `status is ${args.competitionStatus}, not OPEN` });
  }

  if (!args.configValid) {
    blockers.push({ code: 'CONFIG_INVALID', detail: args.configDetail ?? 'competition configuration is invalid' });
  }

  // A competition that cannot judge its own skill question must not reach a
  // draw: the skill element is what makes this a competition rather than a
  // lottery, and an UNJUDGED entry cannot be included or excluded honestly.
  const unjudged = await args.db.prepare(COUNT_UNJUDGED).bind(args.competitionId).first<{ n: number }>();
  if ((unjudged?.n ?? 0) > 0) {
    blockers.push({
      code: 'UNJUDGED_ALLOCATIONS',
      detail: `${unjudged?.n} allocation(s) have skill_verdict UNJUDGED`,
    });
  }

  // Numbers claimed by an allocation_id with no ledger row: a crash between
  // the claim and the ledger write. Repairable, but never freezable.
  const orphans = await args.db
    .prepare(SELECT_ORPHANED_CLAIMS)
    .all<{ competition_id: string; allocation_id: string; n: number }>();
  const mine = orphans.results.filter((o) => o.competition_id === args.competitionId);
  if (mine.length > 0) {
    blockers.push({
      code: 'ORPHANED_CLAIMS',
      detail: `${mine.length} orphaned allocation id(s) holding ${mine.reduce((t, o) => t + o.n, 0)} number(s)`,
    });
  }

  const counts = await poolCounts(args.db, args.competitionId);
  if (counts.total !== args.expectedCapacity) {
    blockers.push({
      code: 'POOL_SIZE_MISMATCH',
      detail: `pool holds ${counts.total} numbers, competition capacity is ${args.expectedCapacity}`,
    });
  }

  return blockers;
}

export async function freeze(args: {
  db: D1Like;
  competitionId: string;
  competitionStatus: string;
  expectedCapacity: number;
  configValid: boolean;
  configDetail?: string;
  now: string;
}): Promise<FreezeResult> {
  const blockers = await freezeBlockers(args);
  if (blockers.length > 0) return { ok: false, blockers };

  // Guarded in SQL too (status = 'OPEN'), so a concurrent second call matches
  // no row rather than freezing twice.
  const row = await args.db
    .prepare(FREEZE_COMPETITION)
    .bind(args.competitionId, args.now)
    .first<{ frozen_at: string }>();

  if (!row) {
    return { ok: false, blockers: [{ code: 'NOT_OPEN', detail: 'competition was not OPEN at the moment of freezing' }] };
  }

  const counts = await poolCounts(args.db, args.competitionId);
  return { ok: true, frozenAt: row.frozen_at, eligible: counts.allocated };
}
