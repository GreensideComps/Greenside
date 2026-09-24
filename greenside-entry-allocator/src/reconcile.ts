/**
 * Reconciliation sweep: the backstop for webhooks that never arrived, or
 * arrived while the Worker was in dry run and were acknowledged unprocessed.
 *
 * STATELESS. Each run covers a fixed look-back chosen by which cron fired:
 *
 *   trailing  every 15 minutes, orders UPDATED in the last 2 hours. Every
 *             business change (payment, refund, cancellation, edit) bumps
 *             updatedAt, so a missed delivery is picked up within one run.
 *   deep      once a day, orders CREATED in the last 59 days: the whole window
 *             read_orders can see. Heals anything the trailing pass missed,
 *             such as a cron outage longer than 2 hours.
 *
 * COMPARE FIRST. The listing carries enough of each order to predict what
 * convergence would do (assessOrder). Only an order that has drifted from the
 * pool is re-read and converged, through the SAME processOrder a webhook uses.
 * Idempotency is therefore the webhook path's: convergence to currentQuantity,
 * target-relative SQL and NOT EXISTS event guards. Running this any number of
 * times, or alongside a webhook for the same order, lands in the same state.
 *
 * REPORT MODE (dry run) reads Shopify and D1 and logs what it would change. It
 * never calls processOrder, so it never writes.
 */

import { numericId } from './config';
import { SELECT_AGED_HELD, SELECT_ALLOCATIONS_FOR_ORDERS, SELECT_RECONCILE_COMPETITIONS } from './db';
import { evaluateOrder } from './eligibility';
import type { ReleaseReason } from './events';
import { effectiveQuantity, processOrder, type ProcessDeps } from './process';
import type { ReconcileOrderNode, ReconcileSortKey } from './shopify';

export type ReconcileMode = 'trailing' | 'deep';

/** The cron expression that selects a deep run when RECONCILE_DEEP_CRON is unset. */
export const DEFAULT_DEEP_CRON = '0 3 * * *';

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

export const TRAILING_WINDOW_MS = 2 * HOUR_MS;
/** One day inside the 60 days of orders read_orders can see. */
export const DEEP_WINDOW_MS = 59 * DAY_MS;
/** Held numbers on an order this old are about to become unverifiable. */
export const AGED_HELD_MS = 55 * DAY_MS;
/** Aged allocations logged individually per run; the rest are counted. */
const AGED_LOG_LIMIT = 20;

export interface ReconcileBudget {
  /** Listing pages (25 orders each) per run. */
  maxPages: number;
  /** processOrder calls per run. */
  maxConverge: number;
}

/**
 * Sized for the Workers Paid plan's per-invocation limits: 1000 subrequests
 * and 1000 D1 queries. A page costs one Shopify call and at most one D1 read;
 * a convergence up to three Shopify calls (order, and product twice on a first
 * allocation) and about twelve D1 queries. Worst case, deep: 200 + 50 x 12 =
 * 800 queries. Whatever a run leaves is picked up by the next.
 */
export const DEFAULT_BUDGETS: Record<ReconcileMode, ReconcileBudget> = {
  trailing: { maxPages: 20, maxConverge: 50 },
  deep: { maxPages: 200, maxConverge: 50 },
};

export interface ReconcileOptions {
  mode: ReconcileMode;
  /** true in dry run: log drift, write nothing. */
  report: boolean;
  budget?: ReconcileBudget;
}

export interface ReconcileSummary {
  mode: ReconcileMode;
  report: boolean;
  search: string;
  pages: number;
  orders_seen: number;
  in_scope: number;
  /** Orders whose listing disagreed with the pool. */
  mismatched: number;
  /** Orders where processOrder claimed or released at least one number. */
  converged_orders: number;
  claimed: number;
  released: number;
  /** Lines owed numbers in a competition that is no longer OPEN. */
  refused_not_open: number;
  unreadable: number;
  errors: number;
  truncated: boolean;
  aged_held: number;
}

/** The sweep's own identity: it records as the reconciler and has no webhook. */
export type ReconcileDeps = Omit<ProcessDeps, 'actor' | 'webhookId'>;

/** A listing timestamp in the form Shopify's search syntax documents. */
function shopifyTimestamp(ms: number): string {
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

export function reconcileSearch(mode: ReconcileMode, nowMs: number): { search: string; sortKey: ReconcileSortKey } {
  if (mode === 'deep') {
    return { search: `created_at:>='${shopifyTimestamp(nowMs - DEEP_WINDOW_MS)}'`, sortKey: 'CREATED_AT' };
  }
  return { search: `updated_at:>='${shopifyTimestamp(nowMs - TRAILING_WINDOW_MS)}'`, sortKey: 'UPDATED_AT' };
}

/**
 * The sweep has no topic, so the reason a release is recorded under is read
 * from the order: a cancellation outranks a refund, a refund outranks an edit.
 */
export function releaseReasonFor(order: Pick<ReconcileOrderNode, 'cancelledAt' | 'refunds'>): ReleaseReason {
  if (order.cancelledAt) return 'CANCELLED';
  if (order.refunds.length > 0) return 'REFUND';
  return 'ORDER_EDIT';
}

export interface AllocationView {
  allocation_id: string;
  competition_id: string;
  order_id: string;
  line_item_id: string;
  entries_per_unit: number;
  held: number;
}

export type DriftAction = 'CLAIM' | 'RELEASE' | 'REREAD' | 'REFUSED_NOT_OPEN';

export interface LineDrift {
  lineItemId: string | null;
  competitionId: string | null;
  action: DriftAction;
  /** null when the target depends on variant config not yet snapshotted. */
  target: number | null;
  held: number;
}

function allocationKey(orderId: string, lineItemId: string): string {
  return `${orderId}:${lineItemId}`;
}

/**
 * Pure: how this order differs from the pool, predicted with the same
 * eligibility rules and quantity rule processLine applies. An empty result
 * means convergence would change nothing, so the order is not re-read.
 */
export function assessOrder(
  order: ReconcileOrderNode,
  competitions: ReadonlyMap<string, string>,
  allocations: ReadonlyMap<string, AllocationView>,
): LineDrift[] {
  // A competition line could sit beyond the listing's 20-line page. The full
  // re-read (up to ORDER_QUERY's 50 lines) is the only way to be sure.
  if (order.lineItems.pageInfo.hasNextPage) {
    return [{ lineItemId: null, competitionId: null, action: 'REREAD', target: null, held: 0 }];
  }

  const orderId = numericId(order.id);
  const drifts: LineDrift[] = [];
  for (const line of order.lineItems.nodes) {
    if (!line.product) continue;
    const competitionId = numericId(line.product.id);
    const status = competitions.get(competitionId);
    if (!status) continue;

    const lineItemId = numericId(line.id);
    const verdict = evaluateOrder(
      { financialStatus: order.displayFinancialStatus, cancelledAt: order.cancelledAt, test: order.test },
      status,
    );
    // Held orders (PENDING, AUTHORIZED, ...) are not converged by processLine
    // either: it records ORDER_HELD and stops.
    if (!verdict.allocate && !verdict.converge) continue;

    const quantity = effectiveQuantity(verdict, line.currentQuantity);
    const allocation = allocations.get(allocationKey(orderId, lineItemId));

    if (!allocation) {
      if (quantity === 0) continue;
      drifts.push({
        lineItemId,
        competitionId,
        action: verdict.allocate ? 'CLAIM' : 'REFUSED_NOT_OPEN',
        target: null,
        held: 0,
      });
      continue;
    }

    const target = quantity * allocation.entries_per_unit;
    if (allocation.held === target) continue;
    if (allocation.held < target && status !== 'OPEN') {
      // The claim SQL refuses anything but OPEN, so converging cannot help.
      drifts.push({ lineItemId, competitionId, action: 'REFUSED_NOT_OPEN', target, held: allocation.held });
      continue;
    }
    drifts.push({
      lineItemId,
      competitionId,
      action: allocation.held < target ? 'CLAIM' : 'RELEASE',
      target,
      held: allocation.held,
    });
  }
  return drifts;
}

function inScope(order: ReconcileOrderNode, competitions: ReadonlyMap<string, string>): boolean {
  if (order.lineItems.pageInfo.hasNextPage) return true;
  return order.lineItems.nodes.some((l) => l.product !== null && competitions.has(numericId(l.product.id)));
}

async function loadAllocations(
  deps: ReconcileDeps,
  orders: ReconcileOrderNode[],
): Promise<Map<string, AllocationView>> {
  const ids = JSON.stringify(orders.map((o) => numericId(o.id)));
  const { results } = await deps.db.prepare(SELECT_ALLOCATIONS_FOR_ORDERS).bind(ids).all<AllocationView>();
  return new Map(
    results.map((r) => [
      allocationKey(String(r.order_id), String(r.line_item_id)),
      { ...r, entries_per_unit: Number(r.entries_per_unit), held: Number(r.held) },
    ]),
  );
}

export async function runReconcile(base: ReconcileDeps, opts: ReconcileOptions): Promise<ReconcileSummary> {
  const deps: ProcessDeps = { ...base, actor: 'system:reconcile', webhookId: null };
  const { logger } = deps;
  const budget = opts.budget ?? DEFAULT_BUDGETS[opts.mode];
  const nowMs = Date.parse(deps.now());
  const { search, sortKey } = reconcileSearch(opts.mode, nowMs);

  const summary: ReconcileSummary = {
    mode: opts.mode,
    report: opts.report,
    search,
    pages: 0,
    orders_seen: 0,
    in_scope: 0,
    mismatched: 0,
    converged_orders: 0,
    claimed: 0,
    released: 0,
    refused_not_open: 0,
    unreadable: 0,
    errors: 0,
    truncated: false,
    aged_held: 0,
  };

  const { results: competitionRows } = await deps.db
    .prepare(SELECT_RECONCILE_COMPETITIONS)
    .all<{ competition_id: string; status: string }>();
  const competitions = new Map(competitionRows.map((r) => [String(r.competition_id), r.status]));

  // Nothing to reconcile against: no Shopify reads at all.
  const seen = new Set<string>();
  let converges = 0;
  let cursor: string | null = null;
  listing: while (competitions.size > 0) {
    if (summary.pages >= budget.maxPages) {
      summary.truncated = true;
      break;
    }
    const page = await deps.shopify.listReconcileOrders(search, sortKey, cursor);
    summary.pages += 1;

    // Sorting by updatedAt, an order updated mid-sweep can reappear on a later
    // page. Once per run is enough.
    const fresh = page.nodes.filter((o) => !seen.has(o.id));
    for (const o of fresh) seen.add(o.id);
    summary.orders_seen += fresh.length;

    const relevant = fresh.filter((o) => inScope(o, competitions));
    summary.in_scope += relevant.length;
    const allocations = relevant.length > 0 ? await loadAllocations(deps, relevant) : new Map<string, AllocationView>();

    for (const order of relevant) {
      const drifts = assessOrder(order, competitions, allocations);
      if (drifts.length === 0) continue;

      for (const d of drifts.filter((x) => x.action === 'REFUSED_NOT_OPEN')) {
        summary.refused_not_open += 1;
        logger.warn('reconcile_refused_not_open', {
          order_gid: order.id,
          line_item_id: d.lineItemId,
          competition_id: d.competitionId,
          status: d.competitionId ? competitions.get(d.competitionId) : null,
          target: d.target,
          held: d.held,
        });
      }
      const actionable = drifts.filter((x) => x.action !== 'REFUSED_NOT_OPEN');
      if (actionable.length === 0) continue;

      summary.mismatched += 1;
      const reason = releaseReasonFor(order);

      if (opts.report) {
        logger.info('reconcile_would_change', { order_gid: order.id, reason, lines: actionable });
        continue;
      }

      if (converges >= budget.maxConverge) {
        summary.truncated = true;
        break listing;
      }
      converges += 1;

      try {
        const outcomes = await processOrder(deps, order.id, reason);
        if (outcomes.length === 0) {
          // processOrder has logged order_unreadable. Nothing was released.
          summary.unreadable += 1;
          continue;
        }
        const claimed = outcomes.reduce((n, o) => n + o.claimed.length, 0);
        const released = outcomes.reduce((n, o) => n + o.released.length, 0);
        summary.claimed += claimed;
        summary.released += released;
        if (claimed + released > 0) summary.converged_orders += 1;
        logger.info('reconcile_converged', {
          order_gid: order.id,
          reason,
          outcomes: outcomes.map((o) => ({
            line_item_id: o.lineItemId,
            competition_id: o.competitionId,
            action: o.action,
            claimed: o.claimed,
            released: o.released,
            detail: o.detail,
          })),
        });
      } catch (err) {
        // One order must not stop the sweep. The next run retries it: the
        // listing will still show the drift.
        summary.errors += 1;
        logger.error('reconcile_order_failed', {
          order_gid: order.id,
          error: String((err as Error)?.message ?? err),
        });
      }
    }

    if (!page.pageInfo.hasNextPage || !page.pageInfo.endCursor) break;
    cursor = page.pageInfo.endCursor;
  }

  // Read-only: numbers on an order approaching the 60-day read horizon.
  const { results: aged } = await deps.db
    .prepare(SELECT_AGED_HELD)
    .bind(new Date(nowMs - AGED_HELD_MS).toISOString())
    .all<Record<string, unknown>>();
  summary.aged_held = aged.length;
  for (const row of aged.slice(0, AGED_LOG_LIMIT)) logger.warn('reconcile_aged_allocation', row);

  if (summary.truncated) {
    logger.warn('reconcile_truncated', { mode: opts.mode, pages: summary.pages, converges, budget });
  }
  logger.info('reconcile_summary', { ...summary });
  return summary;
}
