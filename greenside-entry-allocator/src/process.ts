/**
 * Processing one order.
 *
 * A webhook is a TRIGGER, never a source of truth. Every path here takes only
 * the order id from the payload, re-reads the canonical order from the Admin
 * API and decides from live state. That single choice buys four things:
 * delivery order stops mattering, which topic fired stops mattering, payload
 * staleness becomes impossible, and payload trimming stops mattering.
 */

import { claimLowest, countHeld } from './allocate';
import { buildCompetitionConfig, entriesPerUnit, numericId, type CompetitionConfig } from './config';
import { converge, targetCount } from './converge';
import {
  INSERT_ALLOCATION,
  INSERT_EVENT,
  SELECT_ALLOCATION,
  SELECT_COMPETITION,
  UPDATE_ALLOCATION_COUNTS,
  type D1Like,
} from './db';
import { evaluateOrder } from './eligibility';
import type { Actor, EventRecord, ReleaseReason } from './events';
import { allocationId } from './idempotency';
import type { Logger } from './logging';
import { snapshotSkill } from './skill';
import type { OrderLineItemNode, OrderNode, ShopifyClient } from './shopify';

/** Money as pence. '2.49' -> 249. Avoids float drift on a legal record. */
export function toMinorUnits(amount: string): number {
  const [whole = '0', frac = ''] = amount.split('.');
  const pence = (frac + '00').slice(0, 2);
  const sign = whole.trim().startsWith('-') ? -1 : 1;
  return sign * (Math.abs(Number.parseInt(whole, 10)) * 100 + Number.parseInt(pence, 10));
}

export function propertyValue(line: OrderLineItemNode, key: string): string | null {
  return line.customAttributes.find((a) => a.key === key)?.value ?? null;
}

export interface CompetitionRow {
  competition_id: string;
  status: string;
  capacity: number;
  prefix: string;
  start_number: number;
  pad_width: number;
  skill_answer_correct_snapshot: string | null;
}

export interface ProcessDeps {
  db: D1Like;
  shopify: ShopifyClient;
  logger: Logger;
  shopDomain: string;
  runId: string;
  actor: Actor;
  now: () => string;
  webhookId?: string | null;
}

export interface LineOutcome {
  lineItemId: string;
  competitionId: string | null;
  action: string;
  claimed: string[];
  released: string[];
  detail: string;
}

export async function writeEvent(db: D1Like, e: EventRecord): Promise<void> {
  await db
    .prepare(INSERT_EVENT)
    .bind(
      e.occurredAt,
      e.competitionId,
      e.seq ?? null,
      e.entryNumber ?? null,
      e.allocationId ?? null,
      e.allocationSeq ?? null,
      e.eventType,
      e.fromStatus ?? null,
      e.toStatus ?? null,
      e.orderId ?? null,
      e.customerRef ?? null,
      e.reason ?? null,
      e.actor,
      e.runId,
      e.webhookId ?? null,
      JSON.stringify(e.detail ?? {}),
    )
    .run();
}

/**
 * Process every competition line on one order.
 *
 * `reason` describes why this run is happening, and is recorded on any
 * release it performs.
 */
export async function processOrder(deps: ProcessDeps, orderGid: string, reason: ReleaseReason): Promise<LineOutcome[]> {
  const order = await deps.shopify.fetchOrder(orderGid);
  if (!order) return [];
  const outcomes: LineOutcome[] = [];

  for (const line of order.lineItems.nodes) {
    outcomes.push(await processLine(deps, order, line, reason));
  }
  return outcomes;
}

async function processLine(
  deps: ProcessDeps,
  order: OrderNode,
  line: OrderLineItemNode,
  reason: ReleaseReason,
): Promise<LineOutcome> {
  const now = deps.now();
  const orderId = numericId(order.id);
  const lineItemId = numericId(line.id);
  const base = { lineItemId, competitionId: null as string | null, claimed: [], released: [] };

  if (!line.product) {
    return { ...base, action: 'SKIPPED', detail: 'line item has no product (deleted or custom line)' };
  }
  const competitionId = numericId(line.product.id);

  // Is this a competition the allocator knows about?
  const competition = await deps.db.prepare(SELECT_COMPETITION).bind(competitionId).first<CompetitionRow>();
  if (!competition) {
    return { ...base, competitionId, action: 'SKIPPED', detail: 'product is not a registered competition' };
  }

  const verdict = evaluateOrder(
    { financialStatus: order.displayFinancialStatus, cancelledAt: order.cancelledAt, test: order.test },
    competition.status,
  );

  const allocId = await allocationId(deps.shopDomain, orderId, lineItemId);
  const existing = await deps.db.prepare(SELECT_ALLOCATION).bind(allocId).first<Record<string, unknown>>();
  const held = await countHeld(deps.db, competitionId, allocId);

  // Not eligible and nothing held: record and stop. A PENDING order holding
  // nothing is simply not ready.
  if (!verdict.allocate && !verdict.converge) {
    await writeEvent(deps.db, {
      occurredAt: now,
      competitionId,
      allocationId: allocId,
      eventType: 'ORDER_HELD',
      orderId,
      actor: deps.actor,
      runId: deps.runId,
      webhookId: deps.webhookId,
      detail: { code: verdict.code, detail: verdict.detail, held },
    });
    return { ...base, competitionId, action: 'HELD', detail: `${verdict.code}: ${verdict.detail}` };
  }

  const perUnit = existing
    ? Number(existing['entries_per_unit'])
    : entriesPerUnit(await variantEntriesFor(deps, line));

  // Ineligible but holding numbers: release them. This is how a cancelled or
  // refunded order gives its entries back, driven by currentQuantity.
  const currentQuantity = verdict.allocate || verdict.converge ? line.currentQuantity : 0;
  const effectiveQuantity = verdict.code === 'NEVER_CANCELLED' || verdict.code === 'NEVER_VOIDED' || verdict.code === 'NEVER_EXPIRED' || verdict.code === 'NEVER_TEST'
    ? 0
    : currentQuantity;

  // First allocation for this line: write the ledger row before converging, so
  // the skill verdict is snapshotted from the config as it stands NOW.
  if (!existing && verdict.allocate) {
    const created = await createAllocation(deps, {
      allocId,
      competition,
      competitionId,
      order,
      line,
      perUnit,
      now,
      verdictCode: verdict.code,
    });
    if (!created) {
      return { ...base, competitionId, action: 'SKIPPED', detail: 'competition config unavailable' };
    }
  }

  const outcome = await converge({
    db: deps.db,
    competitionId,
    competitionStatus: competition.status,
    allocationId: allocId,
    currentQuantity: effectiveQuantity,
    entriesPerUnit: perUnit,
    now,
    reason,
    orderId,
    lineItemId,
    customerRef: order.customer ? numericId(order.customer.id) : null,
  });

  for (const c of outcome.claimed) {
    await writeEvent(deps.db, {
      occurredAt: now, competitionId, seq: c.seq, entryNumber: c.entry_number,
      allocationId: allocId, allocationSeq: c.allocation_seq,
      eventType: 'ALLOCATED', fromStatus: 'AVAILABLE', toStatus: 'ALLOCATED',
      orderId, actor: deps.actor, runId: deps.runId, webhookId: deps.webhookId, detail: {},
    });
  }
  for (const r of outcome.released) {
    const returned = competition.status === 'OPEN';
    await writeEvent(deps.db, {
      occurredAt: now, competitionId, seq: r.seq, entryNumber: r.entry_number,
      allocationId: allocId, allocationSeq: r.allocation_seq,
      eventType: 'RELEASED', fromStatus: 'ALLOCATED', toStatus: returned ? 'AVAILABLE' : 'RELEASED',
      orderId, reason, actor: deps.actor, runId: deps.runId, webhookId: deps.webhookId, detail: {},
    });
    if (returned) {
      await writeEvent(deps.db, {
        occurredAt: now, competitionId, seq: r.seq, entryNumber: r.entry_number,
        eventType: 'RETURNED_TO_POOL', fromStatus: 'RELEASED', toStatus: 'AVAILABLE',
        actor: deps.actor, runId: deps.runId, webhookId: deps.webhookId, detail: { reason },
      });
    }
  }
  if (outcome.refusedCapacity) {
    await writeEvent(deps.db, {
      occurredAt: now, competitionId, allocationId: allocId,
      eventType: 'REFUSED_CAPACITY', orderId, actor: deps.actor, runId: deps.runId,
      webhookId: deps.webhookId,
      detail: { requested: outcome.plan.delta, target: outcome.plan.target, held: outcome.plan.held },
    });
  }

  const finalHeld = await countHeld(deps.db, competitionId, allocId);
  const target = targetCount(effectiveQuantity, perUnit);
  const status = finalHeld === 0 ? 'RELEASED' : finalHeld < target ? 'PARTIAL' : 'ALLOCATED';
  await deps.db.prepare(UPDATE_ALLOCATION_COUNTS).bind(allocId, target, finalHeld, status, now).run();

  return {
    lineItemId,
    competitionId,
    action: outcome.plan.action,
    claimed: outcome.claimed.map((c) => c.entry_number),
    released: outcome.released.map((r) => r.entry_number),
    detail: `${verdict.code}; target ${outcome.plan.target}, held ${outcome.plan.held} -> ${finalHeld}`,
  };
}

async function variantEntriesFor(deps: ProcessDeps, line: OrderLineItemNode): Promise<string | null> {
  if (!line.product) return null;
  const product = await deps.shopify.fetchCompetitionProduct(line.product.id);
  if (!product) return null;
  const variant = product.variants.nodes.find((v) => v.id === line.variant?.id);
  return variant?.entries?.value ?? null;
}

async function createAllocation(
  deps: ProcessDeps,
  args: {
    allocId: string;
    competition: CompetitionRow;
    competitionId: string;
    order: OrderNode;
    line: OrderLineItemNode;
    perUnit: number;
    now: string;
    verdictCode: string;
  },
): Promise<boolean> {
  const { line, order } = args;
  if (!line.product) return false;

  const product = await deps.shopify.fetchCompetitionProduct(line.product.id);
  if (!product) return false;

  const configResult = buildCompetitionConfig({
    productGid: product.id,
    handle: product.handle,
    title: product.title,
    productStatus: product.status,
    entriesTotal: product.entriesTotal?.value ?? null,
    entryPrefix: product.entryPrefix?.value ?? null,
    entryStartNumber: product.entryStartNumber?.value ?? null,
    skillQuestion: product.skillQuestion?.value ?? null,
    skillAnswers: product.skillAnswers?.value ?? null,
    skillAnswerCorrect: product.skillAnswerCorrect?.value ?? null,
  });

  // The correct answer is snapshotted HERE and never re-read. A merchant
  // correcting a typo six weeks in must not silently re-judge past entries.
  const correct = configResult.ok ? configResult.config.skill.correct : product.skillAnswerCorrect?.value ?? null;

  const skill = snapshotSkill({
    question: propertyValue(line, '_skill_question'),
    answer: propertyValue(line, 'Skill answer'),
    correct,
    now: args.now,
  });

  await deps.db
    .prepare(INSERT_ALLOCATION)
    .bind(
      args.allocId,
      deps.shopDomain,
      args.competitionId,
      numericId(order.id),
      order.name,
      order.createdAt,
      numericId(line.id),
      line.variant ? numericId(line.variant.id) : null,
      order.customer ? numericId(order.customer.id) : null,
      propertyValue(line, '_entry_route') ?? 'unknown',
      line.quantity,
      args.perUnit,
      0,
      0,
      skill.question,
      skill.answer,
      skill.correctSnapshot,
      skill.verdict,
      skill.judgedAt,
      skill.ruleVersion,
      toMinorUnits(line.discountedUnitPriceAfterAllDiscountsSet.shopMoney.amount),
      // withCodeDiscounts: true -- see the note in shopify.ts.
      toMinorUnits(line.discountedTotalSet.shopMoney.amount),
      JSON.stringify({
        financial_status: order.displayFinancialStatus,
        cancelled_at: order.cancelledAt,
        test: order.test,
        verdict: args.verdictCode,
      }),
      'ALLOCATED',
      deps.actor === 'system:reconcile' ? 'reconcile' : 'webhook',
      args.now,
    )
    .run();

  if (skill.verdict === 'UNJUDGED') {
    await writeEvent(deps.db, {
      occurredAt: args.now, competitionId: args.competitionId, allocationId: args.allocId,
      eventType: 'UNJUDGED_SKILL', orderId: numericId(order.id),
      actor: deps.actor, runId: deps.runId, webhookId: deps.webhookId,
      detail: { note: 'custom.skill_answer_correct is not set; this competition cannot be frozen' },
    });
  } else if (skill.verdict === 'INCORRECT') {
    await writeEvent(deps.db, {
      occurredAt: args.now, competitionId: args.competitionId, allocationId: args.allocId,
      eventType: 'INCORRECT_SKILL', orderId: numericId(order.id),
      actor: deps.actor, runId: deps.runId, webhookId: deps.webhookId,
      detail: { note: 'entry is allocated but excluded from the draw; numbers are NOT voided' },
    });
  }

  return true;
}
