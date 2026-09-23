/**
 * Whether an order may receive entry numbers.
 *
 * Pure functions. The clock and the order are passed in, so every rule here is
 * directly testable and the same inputs always give the same verdict.
 *
 * The default answer is NO. Anything unrecognised, missing or ambiguous holds.
 *
 * Three traps, each verified against the live store rather than assumed:
 *
 *   1. NEVER gate on a transaction existing. Order #1006 is PAID at GBP 0.00
 *      with `transactions: []` and `paymentGatewayNames: []`. Gating on a
 *      transaction would miss every free entry, including the legally required
 *      postal route.
 *
 *   2. NEVER gate on `unpaid`. #1006 reports unpaid: true while being PAID,
 *      because no money was received.
 *
 *   3. ALWAYS check cancelledAt separately. Order #1003 was cancelled and
 *      still reports displayFinancialStatus: PAID.
 */

export type FinancialStatus =
  | 'PENDING'
  | 'AUTHORIZED'
  | 'PARTIALLY_PAID'
  | 'PAID'
  | 'PARTIALLY_REFUNDED'
  | 'REFUNDED'
  | 'VOIDED'
  | 'EXPIRED';

export type EligibilityCode =
  | 'ELIGIBLE'
  | 'HOLD_PENDING'
  | 'HOLD_AUTHORIZED'
  | 'HOLD_PARTIALLY_PAID'
  | 'CONVERGE_ONLY'
  | 'NEVER_VOIDED'
  | 'NEVER_EXPIRED'
  | 'NEVER_CANCELLED'
  | 'NEVER_TEST'
  | 'NEVER_COMPETITION_NOT_OPEN'
  | 'UNKNOWN_STATUS';

export interface OrderFacts {
  financialStatus: string;
  cancelledAt: string | null;
  test: boolean;
}

export interface EligibilityVerdict {
  code: EligibilityCode;
  /** May new numbers be claimed for this order? */
  allocate: boolean;
  /** Should convergence run, i.e. may numbers be RELEASED for this order? */
  converge: boolean;
  /** true only while more information could change the answer. */
  retryable: boolean;
  detail: string;
}

/**
 * PayPal's capture behaviour on this store is deliberately NOT established
 * yet; real-money testing is out of scope until the final pre-launch stage.
 *
 * That is safe precisely because AUTHORIZED holds. An authorisation can still
 * be voided or expire, so money that can vanish must never buy an entry
 * number. If PayPal turns out to authorise rather than capture, the fix is a
 * Shopify capture setting, not a weakening of this rule.
 */
export function evaluateOrder(order: OrderFacts, competitionStatus: string): EligibilityVerdict {
  // Absolute refusals first. These outrank every financial status.
  if (order.test) {
    return { code: 'NEVER_TEST', allocate: false, converge: true, retryable: false, detail: 'test order' };
  }
  if (order.cancelledAt !== null) {
    // Converge still runs: a cancelled order must RELEASE anything it holds.
    return {
      code: 'NEVER_CANCELLED',
      allocate: false,
      converge: true,
      retryable: false,
      detail: `cancelled at ${order.cancelledAt}`,
    };
  }

  switch (order.financialStatus as FinancialStatus) {
    case 'PAID':
      // The only status that buys entry numbers -- and only into an OPEN
      // competition. A FROZEN competition takes no further allocation.
      if (competitionStatus !== 'OPEN') {
        return {
          code: 'NEVER_COMPETITION_NOT_OPEN',
          allocate: false,
          converge: true,
          retryable: false,
          detail: `competition status is ${competitionStatus}, not OPEN`,
        };
      }
      return { code: 'ELIGIBLE', allocate: true, converge: true, retryable: false, detail: 'paid and not cancelled' };

    case 'PENDING':
      // Verified: a manual payment method produces PENDING and only becomes
      // paid when a human marks it so. Money has not arrived.
      return { code: 'HOLD_PENDING', allocate: false, converge: false, retryable: true, detail: 'payment pending' };

    case 'AUTHORIZED':
      return {
        code: 'HOLD_AUTHORIZED',
        allocate: false,
        converge: false,
        retryable: true,
        detail: 'authorised but not captured; money can still be voided',
      };

    case 'PARTIALLY_PAID':
      return {
        code: 'HOLD_PARTIALLY_PAID',
        allocate: false,
        converge: false,
        retryable: true,
        detail: 'partially paid; should not occur on this store',
      };

    case 'PARTIALLY_REFUNDED':
    case 'REFUNDED':
      // Not an allocation trigger, but convergence must run so the refunded
      // portion is released. If this allocation was never made, convergence
      // reconstructs the true history rather than a timing-dependent net.
      return {
        code: 'CONVERGE_ONLY',
        allocate: competitionStatus === 'OPEN',
        converge: true,
        retryable: false,
        detail: `${order.financialStatus}: converge to currentQuantity`,
      };

    case 'VOIDED':
      return { code: 'NEVER_VOIDED', allocate: false, converge: true, retryable: false, detail: 'authorisation voided' };

    case 'EXPIRED':
      return {
        code: 'NEVER_EXPIRED',
        allocate: false,
        converge: true,
        retryable: false,
        detail: 'authorisation expired uncaptured',
      };

    default:
      return {
        code: 'UNKNOWN_STATUS',
        allocate: false,
        converge: false,
        retryable: true,
        detail: `unrecognised financial status ${JSON.stringify(order.financialStatus)}`,
      };
  }
}
