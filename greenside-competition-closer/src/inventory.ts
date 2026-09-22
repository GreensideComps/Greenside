/**
 * The close operation for a single competition.
 *
 * Sequence: read -> (set to zero, compare-and-set) -> verify by fresh read ->
 * tag. The tag is added only after a verified zero, which is what makes a
 * partially failed run recoverable: a competition left zeroed but untagged is
 * picked up by the next run, which skips the mutation and completes the tag.
 */

import type { Logger, FinalState } from './logging';
import type { CloserConfig, EligibleTarget } from './eligibility';
import type { ShopifyClient } from './shopify';
import { ShopifyError } from './shopify';

/** The only quantity this Worker ever writes. Never configurable. */
export const TARGET_QUANTITY = 0;

export const MAX_SET_ATTEMPTS = 3;

/**
 * User errors that mean "your view of inventory was stale, look again".
 * Both spellings exist in the schema during Shopify's field rename.
 */
const RETRYABLE_USER_ERROR_CODES = new Set([
  'CHANGE_FROM_QUANTITY_STALE',
  'COMPARE_QUANTITY_STALE',
  'IDEMPOTENCY_CONCURRENT_REQUEST',
  'IDEMPOTENCY_PREVIOUS_ATTEMPT_FAILED',
  'IDEMPOTENCY_KEY_PARAMETER_MISMATCH',
]);

export interface CloseOutcome {
  finalState: FinalState;
  currentInventory: number | null;
  targetInventory: number;
  inventoryMutationResult: string;
  verificationResult: string;
  tagResult: string;
  errorCode: string | null;
  attempts: number;
}

/**
 * A deterministic, UUID-shaped idempotency key.
 *
 * Shopify requires the @idempotent directive on inventorySetQuantities from
 * API 2026-04 and recommends a UUID. It also rejects a reused key whose
 * arguments differ (IDEMPOTENCY_KEY_PARAMETER_MISMATCH), so the observed
 * quantity is part of the identity: a genuine duplicate of the same attempt
 * reuses the key and is replayed, while a compare-and-set retry against a
 * newly observed quantity is a different operation and gets its own key.
 *
 * SHA-256 of the identity string, formatted as a v8 (custom) UUID.
 */
export async function buildIdempotencyKey(
  productId: string,
  closingAtIso: string,
  changeFromQuantity: number,
): Promise<string> {
  const identity = `competition-close:${productId}:${closingAtIso}:${changeFromQuantity}`;
  const digest = new Uint8Array(
    await crypto.subtle.digest('SHA-256', new TextEncoder().encode(identity)),
  ).slice(0, 16);

  digest[6] = (digest[6] & 0x0f) | 0x80; // version 8
  digest[8] = (digest[8] & 0x3f) | 0x80; // RFC 4122 variant

  const hex = Array.from(digest, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Audit URI so "greenside-competition-closer" appears in Shopify's inventory history. */
export function referenceDocumentUri(productId: string): string {
  const numeric = productId.split('/').pop() ?? productId;
  return `gid://greenside-competition-closer/CompetitionClose/${numeric}`;
}

export interface CloseDeps {
  client: ShopifyClient;
  logger: Logger;
  config: CloserConfig;
  dryRun: boolean;
}

/**
 * Drive one eligible competition to CLOSED, or to a named failure state.
 *
 * Callers must only pass a target that eligibility.ts approved. The location
 * guard below is a second, independent check: this function will not write to
 * any location other than the configured one, whatever it is handed.
 */
export async function closeCompetition(
  deps: CloseDeps,
  product: { id: string; title: string },
  target: EligibleTarget,
  closingAtIso: string,
): Promise<CloseOutcome> {
  const { client, logger, config, dryRun } = deps;

  const outcome: CloseOutcome = {
    finalState: 'CLOSE_FAILED',
    currentInventory: target.available,
    targetInventory: TARGET_QUANTITY,
    inventoryMutationResult: 'NOT_ATTEMPTED',
    verificationResult: 'NOT_ATTEMPTED',
    tagResult: 'NOT_ATTEMPTED',
    errorCode: null,
    attempts: 0,
  };

  // Hard guard, independent of the caller's reasoning.
  if (!config.allowedLocationId) {
    outcome.errorCode = 'NO_ALLOWED_LOCATION';
    outcome.finalState = 'SKIPPED';
    return outcome;
  }

  if (dryRun) {
    logger.info('WOULD_CLOSE', {
      product_id: product.id,
      product_title: product.title,
      closing_at: closingAtIso,
      current_inventory: target.available,
      target_inventory: TARGET_QUANTITY,
      would_add_tag: config.closedTag,
    });
    outcome.finalState = 'DRY_RUN_WOULD_CLOSE';
    outcome.inventoryMutationResult = 'DRY_RUN_SKIPPED';
    outcome.verificationResult = 'DRY_RUN_SKIPPED';
    outcome.tagResult = 'DRY_RUN_SKIPPED';
    return outcome;
  }

  let observed = target.available;

  if (observed === TARGET_QUANTITY) {
    // Already zero. This is the recovery path for a run that zeroed inventory
    // but failed before tagging. Do not mutate again.
    outcome.inventoryMutationResult = 'SKIPPED_ALREADY_ZERO';
  } else {
    let succeeded = false;

    for (let attempt = 1; attempt <= MAX_SET_ATTEMPTS; attempt++) {
      outcome.attempts = attempt;
      const idempotencyKey = await buildIdempotencyKey(product.id, closingAtIso, observed);

      let result;
      try {
        result = await client.setAvailableQuantity({
          inventoryItemId: target.inventoryItemId,
          locationId: config.allowedLocationId,
          quantity: TARGET_QUANTITY,
          changeFromQuantity: observed,
          referenceDocumentUri: referenceDocumentUri(product.id),
          idempotencyKey,
        });
      } catch (err) {
        const e = err instanceof ShopifyError ? err : null;
        outcome.errorCode = e?.kind ?? 'UNKNOWN';
        outcome.inventoryMutationResult = `TRANSPORT_ERROR:${outcome.errorCode}`;
        logger.error('inventory_mutation_transport_error', {
          product_id: product.id,
          attempt,
          error_kind: outcome.errorCode,
        });
        return outcome; // client already exhausted its own bounded retries
      }

      if (result.userErrors.length === 0) {
        outcome.inventoryMutationResult = 'SET_TO_ZERO';
        succeeded = true;
        break;
      }

      const codes = result.userErrors.map((e) => e.code ?? 'UNKNOWN');
      const retryable = codes.some((c) => RETRYABLE_USER_ERROR_CODES.has(c));
      logger.warn('inventory_mutation_user_error', {
        product_id: product.id,
        attempt,
        codes,
        retryable,
      });

      if (!retryable) {
        outcome.errorCode = codes[0] ?? 'USER_ERROR';
        outcome.inventoryMutationResult = `USER_ERROR:${outcome.errorCode}`;
        return outcome;
      }

      outcome.errorCode = codes[0] ?? 'CONFLICT';
      if (attempt === MAX_SET_ATTEMPTS) break;

      // Re-read so the next compare-and-set is based on the current truth,
      // which also changes the idempotency identity for the retry.
      const fresh = await client.readAvailable(target.inventoryItemId, config.allowedLocationId);
      if (fresh === null) {
        outcome.errorCode = 'INVENTORY_LEVEL_DISAPPEARED';
        outcome.inventoryMutationResult = 'RE_READ_FAILED';
        return outcome;
      }
      observed = fresh;
      outcome.currentInventory = fresh;

      if (observed === TARGET_QUANTITY) {
        // Someone else (or a concurrent run) got there first.
        outcome.inventoryMutationResult = 'ALREADY_ZERO_AFTER_CONFLICT';
        succeeded = true;
        break;
      }
    }

    if (!succeeded) {
      outcome.inventoryMutationResult = outcome.inventoryMutationResult.startsWith('USER_ERROR')
        ? outcome.inventoryMutationResult
        : 'RETRIES_EXHAUSTED';
      outcome.finalState = 'CLOSE_FAILED';
      return outcome;
    }
  }

  // Verification is mandatory. A successful GraphQL response is not proof.
  const verified = await client.readAvailable(target.inventoryItemId, config.allowedLocationId);
  outcome.currentInventory = verified;
  if (verified !== TARGET_QUANTITY) {
    outcome.verificationResult = `EXPECTED_0_GOT_${verified === null ? 'NULL' : verified}`;
    outcome.finalState = 'VERIFICATION_FAILED';
    outcome.errorCode = 'VERIFICATION_FAILED';
    logger.error('verification_failed', {
      product_id: product.id,
      observed_available: verified,
    });
    return outcome; // no tag
  }
  outcome.verificationResult = 'AVAILABLE_IS_ZERO';

  const tagErrors = await client.addTags(product.id, [config.closedTag]);
  if (tagErrors.length > 0) {
    outcome.tagResult = 'FAILED';
    outcome.finalState = 'TAG_FAILED';
    outcome.errorCode = 'TAG_FAILED';
    logger.error('tag_failed', {
      product_id: product.id,
      messages: tagErrors.map((e) => e.message),
    });
    return outcome;
  }

  outcome.tagResult = 'ADDED';
  outcome.finalState = outcome.inventoryMutationResult === 'SKIPPED_ALREADY_ZERO' ? 'ALREADY_CLOSED' : 'CLOSED';
  return outcome;
}
