/**
 * Convergence: move an allocation's holdings to the number it should have.
 *
 * ONE RULE COVERS EVERY CASE.
 *
 *   target = lineItem.currentQuantity x entries_per_unit
 *
 * `currentQuantity` is documented as "the number of units ordered, excluding
 * refunded and removed units", so it already folds in refunds, cancellations
 * and order-edit removals. Verified live: cancelled order #1003 reports
 * currentQuantity 0 while its financial status is still PAID.
 *
 * Why a target rather than per-event handling:
 *
 *   - It is idempotent. Processing the same refund five times converges once
 *     and then does nothing.
 *   - It is order-independent. A refund event arriving before the paid event
 *     still ends correct, because both recompute the same target.
 *   - A MONEY-ONLY REFUND IS HANDLED FOR FREE. A goodwill refund carries no
 *     refundLineItems, so currentQuantity does not move, so target does not
 *     move, so nothing is released. No special case, no trap.
 */

import { countHeld, type ClaimedNumber } from './allocate';
import {
  CLAIM_TO_TARGET,
  INSERT_ALLOCATED_EVENTS,
  INSERT_RELEASED_EVENTS,
  INSERT_RETURNED_EVENTS,
  RELEASE_TO_TARGET,
  SELECT_CONVERGED,
  UPDATE_ALLOCATION_COUNTS,
  type D1Like,
  type D1StatementLike,
} from './db';
import type { Actor, ReleaseReason } from './events';

export interface ConvergePlan {
  target: number;
  held: number;
  action: 'NONE' | 'ALLOCATE' | 'RELEASE';
  delta: number;
}

/**
 * The quantity source, isolated deliberately.
 *
 * VERIFIED, not assumed. Live order #1009 (GBP 0.00, PAID, quantity 5) was
 * partially refunded by 2 units with NO_RESTOCK, and Shopify reported:
 *
 *   quantity            5 -> 5    the original is IMMUTABLE. It is order
 *                                 history, never an entitlement figure, and
 *                                 reading it here would have released nothing.
 *   currentQuantity     5 -> 3    the entitlement signal, decremented by
 *                                 exactly the refunded units.
 *   refundableQuantity  5 -> 3    tracks currentQuantity.
 *
 *   => targetCount = currentQuantity x entries_per_unit = 3 x 1 = 3.
 *
 * The refund also proved a trap worth naming: it produced a Refund object
 * carrying NO financial transactions at all (the order had none to begin
 * with, being fully discounted), and refunds[].totalRefundedSet was GBP 0.00.
 * A release must therefore never be detected from money movement, refund
 * transactions or a change in financial status -- on a GBP 0.00 order there
 * is none. Only the recomputed currentQuantity moves.
 *
 * Full-quantity refunds and cancellations driving currentQuantity to 0 are
 * separately observed on live orders #1001, #1002, #1003 and #1004.
 *
 * If this behaviour ever changes, this function is the only thing that does.
 * Regression coverage: tests/u2-partial-refund.test.ts.
 */
export function targetCount(currentQuantity: number, entriesPerUnit: number): number {
  const units = Math.max(0, currentQuantity);
  const per = Math.max(1, entriesPerUnit);
  return units * per;
}

export function planConvergence(target: number, held: number): ConvergePlan {
  if (held === target) return { target, held, action: 'NONE', delta: 0 };
  if (held > target) return { target, held, action: 'RELEASE', delta: held - target };
  return { target, held, action: 'ALLOCATE', delta: target - held };
}

export interface ConvergeOutcome {
  plan: ConvergePlan;
  claimed: ClaimedNumber[];
  released: ClaimedNumber[];
  refusedCapacity: boolean;
}

/**
 * Execute a convergence, atomically.
 *
 * Everything this run changes is sent as ONE db.batch(), which D1 commits as a
 * single transaction:
 *
 *   [prelude]                  the ledger row and skill event, first time only
 *   CLAIM_TO_TARGET            or RELEASE_TO_TARGET, or neither
 *   INSERT_ALLOCATED_EVENTS    }
 *   INSERT_RELEASED_EVENTS     }  one event per issue of a number, never twice
 *   INSERT_RETURNED_EVENTS     }
 *   UPDATE_ALLOCATION_COUNTS   held_count read from the pool, not from the plan
 *   SELECT_CONVERGED           status and holdings as committed, for the caller
 *
 * So a failure anywhere leaves nothing behind, and the retry starts from the
 * state before this run. Retry used to repair the numbers and the ledger but
 * never the events: once the claim had committed, held == target, the plan
 * was NONE and the missing events were never written.
 *
 * `competitionStatus` is the caller's earlier read and only decides whether a
 * claim is attempted. The batch itself is authoritative: the claim re-checks
 * that the competition is OPEN, and a release reads the status to decide
 * whether the number returns to the pool, so a freeze that commits after the
 * caller's read is honoured.
 */
export async function converge(args: {
  db: D1Like;
  competitionId: string;
  competitionStatus: string;
  allocationId: string;
  currentQuantity: number;
  entriesPerUnit: number;
  now: string;
  reason: ReleaseReason;
  orderId: string;
  lineItemId: string;
  customerRef: string | null;
  actor: Actor;
  runId: string;
  webhookId?: string | null;
  /** Statements that must commit in the same transaction, ahead of the convergence. */
  prelude?: D1StatementLike[];
}): Promise<ConvergeOutcome> {
  const { db, competitionId, allocationId, now, reason } = args;
  const target = targetCount(args.currentQuantity, args.entriesPerUnit);
  const held = await countHeld(db, competitionId, allocationId);
  const plan = planConvergence(target, held);
  // A claim is not even attempted once the caller has seen the freeze. The
  // SQL enforces the freeze invariant regardless: no claim unless OPEN, and a
  // release after the freeze stays RELEASED for good.
  const open = args.competitionStatus === 'OPEN';

  const statements = [...(args.prelude ?? [])];
  let claimAt = -1;
  let releaseAt = -1;
  if (plan.action === 'ALLOCATE' && open) {
    claimAt = statements.length;
    statements.push(
      db
        .prepare(CLAIM_TO_TARGET)
        .bind(competitionId, allocationId, now, target, args.orderId, args.lineItemId, args.customerRef),
    );
  } else if (plan.action === 'RELEASE') {
    releaseAt = statements.length;
    statements.push(
      db.prepare(RELEASE_TO_TARGET).bind(competitionId, allocationId, now, target, reason),
    );
  }

  const event = [now, args.actor, args.runId, args.webhookId ?? null] as const;
  statements.push(
    db.prepare(INSERT_ALLOCATED_EVENTS).bind(competitionId, allocationId, ...event),
    db.prepare(INSERT_RELEASED_EVENTS).bind(competitionId, allocationId, ...event, reason),
    db.prepare(INSERT_RETURNED_EVENTS).bind(competitionId, allocationId, ...event, reason),
    db.prepare(UPDATE_ALLOCATION_COUNTS).bind(competitionId, allocationId, now, target),
    db.prepare(SELECT_CONVERGED).bind(competitionId, allocationId),
  );

  const results = (await db.batch(statements)) as { results?: unknown[] }[];
  const converged = results[results.length - 1]?.results?.[0] as { competition_status: string; held: number };

  // Ascending, so the caller and the customer see 'PUT1001, PUT1002, PUT1003'.
  const claimed = claimAt < 0 ? [] : rows(results[claimAt]).sort((a, b) => a.seq - b.seq);
  // Descending: highest released first, which is the order they were taken.
  const released = releaseAt < 0 ? [] : rows(results[releaseAt]).sort((a, b) => b.seq - a.seq);

  // Zero rows claimed is a capacity refusal only if the allocation is still
  // short (a concurrent run for the same line may have reached the target
  // first) and the competition is still OPEN (a freeze refuses for its own
  // reason, not for capacity). Both read in the same transaction as the claim.
  const refusedCapacity =
    claimAt >= 0 && claimed.length === 0 && converged.competition_status === 'OPEN' && converged.held < target;

  return { plan, claimed, released, refusedCapacity };
}

function rows(result: { results?: unknown[] } | undefined): ClaimedNumber[] {
  return ((result?.results ?? []) as ClaimedNumber[]).slice();
}
