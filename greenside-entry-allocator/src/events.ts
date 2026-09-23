/**
 * The allocator's event vocabulary.
 *
 * Kept as a closed union so a typo in an event name is a compile error rather
 * than a silently unqueryable row. The values here must stay in step with the
 * CHECK constraint in migrations/0004_entry_event.sql.
 */

export type EventType =
  | 'POOL_BUILT'
  | 'POOL_GROWN'
  | 'POOL_SHRINK_REFUSED'
  | 'ALLOCATED'
  | 'RELEASED'
  | 'RETURNED_TO_POOL'
  | 'CONFIG_INVALID'
  | 'REFUSED_PREFIX_TAKEN'
  | 'REFUSED_CAPACITY'
  | 'INCORRECT_SKILL'
  | 'UNJUDGED_SKILL'
  | 'FROZEN'
  | 'FREEZE_REFUSED'
  | 'WEBHOOK_RECEIVED'
  | 'WEBHOOK_REJECTED'
  | 'WEBHOOK_SKIPPED'
  | 'ORDER_HELD'
  | 'ORDER_INELIGIBLE'
  | 'RECONCILED'
  | 'REPAIRED'
  | 'INVARIANT_VIOLATION'
  | 'MIRRORED'
  | 'MIRROR_FAILED'
  | 'ERROR';

export type Actor = 'system:webhook' | 'system:reconcile' | 'system:cli' | `staff:${string}`;

export type ReleaseReason = 'REFUND' | 'CANCELLED' | 'ORDER_EDIT' | 'CHARGEBACK' | 'MANUAL' | 'REPAIR';

export interface EventRecord {
  occurredAt: string;
  competitionId: string;
  seq?: number | null;
  entryNumber?: string | null;
  allocationId?: string | null;
  allocationSeq?: number | null;
  eventType: EventType;
  fromStatus?: string | null;
  toStatus?: string | null;
  orderId?: string | null;
  customerRef?: string | null;
  reason?: string | null;
  actor: Actor;
  runId: string;
  webhookId?: string | null;
  detail?: Record<string, unknown>;
}
