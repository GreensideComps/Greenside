/**
 * The frozen snapshot -- the ONLY draw-facing surface of the allocator.
 *
 * This module hands over a list and nothing more. It does not hash it, does
 * not pick a winner, knows nothing about randomness beacons, and has no
 * concept of a redraw. Those belong to the future draw project, which will
 * consume this export.
 *
 * Deliberately absent, and to stay absent:
 *   - commitment hashing
 *   - randomness / beacon integration
 *   - winner selection
 *   - verification or public proof generation
 *   - redraw handling
 */

import { SELECT_FROZEN_SNAPSHOT, SELECT_SNAPSHOT_EXCLUSIONS, type D1Like } from './db';

export interface SnapshotEntry {
  entry_number: string;
  seq: number;
  allocation_id: string;
  allocation_seq: number;
  order_id: string;
  order_name: string;
  /**
   * Always NULL at present: the order is read without its customer, which
   * would need read_customers (see ORDER_QUERY). Identify the entrant by
   * order_id. The column and its migration comments predate this and still
   * permit a customer id.
   */
  customer_ref: string | null;
  entry_route: string;
  skill_verdict: string;
}

export interface SnapshotExclusion {
  entry_number: string;
  seq: number;
  skill_verdict: string;
  order_id: string;
}

export interface FrozenSnapshot {
  competitionId: string;
  status: string;
  frozenAt: string | null;
  eligibleCount: number;
  /** Ascending by seq. Ownership is AS AT THE FREEZE, never "current". */
  entries: SnapshotEntry[];
  /** Allocated but not eligible, with the reason. Auditable alongside. */
  exclusions: SnapshotExclusion[];
}

/**
 * Read the eligible entry list.
 *
 * Eligibility is `status = ALLOCATED AND skill_verdict = CORRECT`. Entries
 * excluded for a wrong answer are NOT voided -- the entrant keeps the numbers
 * and the history keeps the answer; the draw simply does not see them.
 */
export async function readSnapshot(args: {
  db: D1Like;
  competitionId: string;
  competitionStatus: string;
  frozenAt: string | null;
}): Promise<FrozenSnapshot> {
  const entries = await args.db.prepare(SELECT_FROZEN_SNAPSHOT).bind(args.competitionId).all<SnapshotEntry>();
  const exclusions = await args.db
    .prepare(SELECT_SNAPSHOT_EXCLUSIONS)
    .bind(args.competitionId)
    .all<SnapshotExclusion>();

  return {
    competitionId: args.competitionId,
    status: args.competitionStatus,
    frozenAt: args.frozenAt,
    eligibleCount: entries.results.length,
    entries: entries.results,
    exclusions: exclusions.results,
  };
}
