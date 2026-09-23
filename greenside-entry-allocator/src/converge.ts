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

import { claimLowest, countHeld, releaseHighest, type ClaimedNumber } from './allocate';
import type { D1Like } from './db';
import type { ReleaseReason } from './events';

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
 * Execute a convergence.
 *
 * `competitionStatus` decides two things at once: whether more numbers may be
 * claimed at all, and whether released numbers return to the pool.
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
}): Promise<ConvergeOutcome> {
  const target = targetCount(args.currentQuantity, args.entriesPerUnit);
  const held = await countHeld(args.db, args.competitionId, args.allocationId);
  const plan = planConvergence(target, held);

  if (plan.action === 'NONE') {
    return { plan, claimed: [], released: [], refusedCapacity: false };
  }

  if (plan.action === 'RELEASE') {
    const released = await releaseHighest({
      db: args.db,
      competitionId: args.competitionId,
      allocationId: args.allocationId,
      count: plan.delta,
      now: args.now,
      reason: args.reason,
      // THE FREEZE INVARIANT, in one place.
      returnToPool: args.competitionStatus === 'OPEN',
    });
    return { plan, claimed: [], released, refusedCapacity: false };
  }

  // ALLOCATE. Only into an OPEN competition -- a frozen entry list is final.
  if (args.competitionStatus !== 'OPEN') {
    return { plan, claimed: [], released: [], refusedCapacity: false };
  }

  const result = await claimLowest({
    db: args.db,
    competitionId: args.competitionId,
    allocationId: args.allocationId,
    count: plan.delta,
    now: args.now,
    orderId: args.orderId,
    lineItemId: args.lineItemId,
    customerRef: args.customerRef,
  });

  if (!result.ok) return { plan, claimed: [], released: [], refusedCapacity: true };
  return { plan, claimed: result.claimed, released: [], refusedCapacity: false };
}
