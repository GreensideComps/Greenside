import { describe, expect, test } from 'vitest';
import { evaluateOrder } from '../src/eligibility';

const open = 'OPEN';
const paid = { financialStatus: 'PAID', cancelledAt: null, test: false };

describe('the safe payment gate', () => {
  test('PAID + not cancelled + not test + OPEN is the only thing that allocates', () => {
    const v = evaluateOrder(paid, open);
    expect(v.code).toBe('ELIGIBLE');
    expect(v.allocate).toBe(true);
  });

  test.each(['PENDING', 'AUTHORIZED', 'PARTIALLY_PAID'])('%s HOLDS and never allocates', (status) => {
    const v = evaluateOrder({ ...paid, financialStatus: status }, open);
    expect(v.allocate).toBe(false);
    expect(v.converge).toBe(false); // holds nothing, releases nothing
    expect(v.retryable).toBe(true);
  });

  test('AUTHORIZED specifically holds — money that can still be voided buys nothing', () => {
    const v = evaluateOrder({ ...paid, financialStatus: 'AUTHORIZED' }, open);
    expect(v.code).toBe('HOLD_AUTHORIZED');
    expect(v.allocate).toBe(false);
  });

  test.each(['VOIDED', 'EXPIRED'])('%s never allocates but does converge, so held numbers are released', (status) => {
    const v = evaluateOrder({ ...paid, financialStatus: status }, open);
    expect(v.allocate).toBe(false);
    expect(v.converge).toBe(true);
    expect(v.retryable).toBe(false);
  });

  test('a cancelled order never allocates, even though it is still PAID', () => {
    // Verified on live order #1003: cancelled, and displayFinancialStatus PAID.
    const v = evaluateOrder({ financialStatus: 'PAID', cancelledAt: '2026-09-21T13:12:50Z', test: false }, open);
    expect(v.code).toBe('NEVER_CANCELLED');
    expect(v.allocate).toBe(false);
    expect(v.converge).toBe(true);
  });

  test('a test order never allocates', () => {
    const v = evaluateOrder({ ...paid, test: true }, open);
    expect(v.code).toBe('NEVER_TEST');
    expect(v.allocate).toBe(false);
  });

  test('cancellation outranks the financial status entirely', () => {
    for (const status of ['PAID', 'PENDING', 'AUTHORIZED', 'REFUNDED', 'VOIDED']) {
      const v = evaluateOrder({ financialStatus: status, cancelledAt: '2026-01-01T00:00:00Z', test: false }, open);
      expect(v.code).toBe('NEVER_CANCELLED');
    }
  });

  test('test outranks everything, including cancellation', () => {
    const v = evaluateOrder({ financialStatus: 'PAID', cancelledAt: '2026-01-01T00:00:00Z', test: true }, open);
    expect(v.code).toBe('NEVER_TEST');
  });
});

describe('refund states converge rather than allocate fresh', () => {
  test.each(['REFUNDED', 'PARTIALLY_REFUNDED'])('%s converges', (status) => {
    const v = evaluateOrder({ ...paid, financialStatus: status }, open);
    expect(v.code).toBe('CONVERGE_ONLY');
    expect(v.converge).toBe(true);
  });
});

describe('competition status', () => {
  test('a PAID order into a FROZEN competition does not allocate', () => {
    const v = evaluateOrder(paid, 'FROZEN');
    expect(v.code).toBe('NEVER_COMPETITION_NOT_OPEN');
    expect(v.allocate).toBe(false);
  });

  test('a PAID order into a DRAFT competition does not allocate', () => {
    expect(evaluateOrder(paid, 'DRAFT').allocate).toBe(false);
  });
});

describe('unknown input', () => {
  test('an unrecognised status holds rather than guessing', () => {
    const v = evaluateOrder({ ...paid, financialStatus: 'SOMETHING_NEW' }, open);
    expect(v.code).toBe('UNKNOWN_STATUS');
    expect(v.allocate).toBe(false);
    expect(v.retryable).toBe(true);
  });
});
