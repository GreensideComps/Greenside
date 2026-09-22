/**
 * Greenside competition closer.
 *
 * A scheduled Worker with exactly one job: when a competition's
 * custom.closing_at has passed, set its available inventory to 0 so Shopify
 * itself refuses further checkout, then tag it so it is never processed again.
 *
 * It only ever closes. It never restores inventory, removes a tag, changes a
 * product's status, or edits closing_at. Reopening is a manual operation.
 */

import { Logger, newRunId, type FinalState } from './logging';
import { ShopifyClient, SHOPIFY_API_VERSION, ShopifyError, type ProductNode } from './shopify';
import { evaluateEligibility, type CloserConfig } from './eligibility';
import { closeCompetition, TARGET_QUANTITY } from './inventory';

export interface Env {
  SHOPIFY_STORE?: string;
  SHOPIFY_ACCESS_TOKEN?: string;
  SHOPIFY_API_VERSION?: string;
  DRY_RUN?: string;
  ALLOWED_LOCATION_ID?: string;
  REQUIRED_LIVE_TAG?: string;
  CLOSED_TAG?: string;
}

/** Hard defaults. Every one of these is a safety boundary, not a preference. */
export const DEFAULTS = {
  allowedLocationId: 'gid://shopify/Location/119443915126',
  requiredLiveTag: 'competition-live',
  closedTag: 'gs-closed-zeroed',
} as const;

/**
 * Dry run is the default and can only be turned off by the exact string
 * "false". An unset, misspelled or empty variable leaves the Worker read-only,
 * so a configuration mistake can never produce a write.
 */
export function isDryRun(env: Env): boolean {
  return env.DRY_RUN !== 'false';
}

export function resolveConfig(env: Env): CloserConfig {
  return {
    allowedLocationId: env.ALLOWED_LOCATION_ID || DEFAULTS.allowedLocationId,
    requiredLiveTag: env.REQUIRED_LIVE_TAG || DEFAULTS.requiredLiveTag,
    closedTag: env.CLOSED_TAG || DEFAULTS.closedTag,
  };
}

/**
 * The discovery search.
 *
 * Deliberately excludes any closing_at predicate: Shopify silently ignores an
 * unsupported metafield filter and returns the unfiltered set, which would
 * look like it worked while closing competitions that are still open. The
 * date comparison is done in the Worker, against every product returned.
 */
export function buildSearchQuery(config: CloserConfig): string {
  return `status:active AND tag:${config.requiredLiveTag} AND -tag:${config.closedTag}`;
}

export interface RunSummary {
  runId: string;
  dryRun: boolean;
  discovered: number;
  states: Record<string, number>;
  errors: number;
}

export interface RunDeps {
  client: ShopifyClient;
  logger: Logger;
  now: () => number;
}

/** One pass over every opted-in competition. */
export async function runCloser(env: Env, deps: RunDeps): Promise<RunSummary> {
  const { client, logger, now } = deps;
  const config = resolveConfig(env);
  const dryRun = isDryRun(env);
  const runId = (logger as unknown as { runId?: string }).runId ?? '';

  // One clock reading for the whole run, so products are judged consistently.
  const nowMs = now();
  const summary: RunSummary = { runId, dryRun, discovered: 0, states: {}, errors: 0 };

  const record = (state: FinalState | string): void => {
    summary.states[state] = (summary.states[state] ?? 0) + 1;
  };

  let products: ProductNode[];
  try {
    products = await client.fetchCandidates(buildSearchQuery(config), config.allowedLocationId);
  } catch (err) {
    const e = err instanceof ShopifyError ? err : null;
    logger.error('discovery_failed', { error_kind: e?.kind ?? 'UNKNOWN', status: e?.status });
    summary.errors += 1;
    return summary;
  }

  summary.discovered = products.length;
  logger.info('discovery_complete', {
    discovered: products.length,
    search: buildSearchQuery(config),
    now_utc: new Date(nowMs).toISOString(),
  });

  for (const product of products) {
    const plog = logger.child({ product_id: product.id, product_title: product.title });
    const verdict = evaluateEligibility(product, nowMs, config);

    if (!verdict.eligible || !verdict.target || !verdict.closingAtIso) {
      plog.info('product_skipped', {
        status: product.status,
        closing_at: verdict.closingAtIso,
        eligibility_result: verdict.code,
        detail: verdict.detail,
        final_state: 'SKIPPED' satisfies FinalState,
      });
      record('SKIPPED');
      continue;
    }

    try {
      const outcome = await closeCompetition(
        { client, logger: plog, config, dryRun },
        product,
        verdict.target,
        verdict.closingAtIso,
      );

      plog.info('product_processed', {
        status: product.status,
        closing_at: verdict.closingAtIso,
        eligibility_result: verdict.code,
        current_inventory: outcome.currentInventory,
        target_inventory: TARGET_QUANTITY,
        inventory_mutation_result: outcome.inventoryMutationResult,
        verification_result: outcome.verificationResult,
        tag_result: outcome.tagResult,
        attempts: outcome.attempts,
        final_state: outcome.finalState,
        error_code: outcome.errorCode,
      });

      record(outcome.finalState);
      if (outcome.errorCode) summary.errors += 1;
    } catch (err) {
      // One product's failure must never abandon the rest of the run.
      const e = err instanceof ShopifyError ? err : null;
      plog.error('product_unhandled_error', {
        error_kind: e?.kind ?? 'UNKNOWN',
        status: e?.status,
        final_state: 'CLOSE_FAILED' satisfies FinalState,
      });
      record('CLOSE_FAILED');
      summary.errors += 1;
    }
  }

  return summary;
}

function buildLogger(env: Env, runId: string): Logger {
  const logger = new Logger(
    { run_id: runId, dry_run: isDryRun(env) },
    [env.SHOPIFY_ACCESS_TOKEN],
  );
  (logger as unknown as { runId: string }).runId = runId;
  return logger;
}

function requireCredentials(env: Env, logger: Logger): { store: string; token: string } | null {
  const store = env.SHOPIFY_STORE?.trim();
  const token = env.SHOPIFY_ACCESS_TOKEN?.trim();
  if (!store || !token) {
    // Names only. The absent value is never echoed.
    logger.error('missing_configuration', {
      missing: [!store ? 'SHOPIFY_STORE' : null, !token ? 'SHOPIFY_ACCESS_TOKEN' : null].filter(Boolean),
    });
    return null;
  }
  return { store, token };
}

export default {
  async scheduled(controller: ScheduledController, env: Env, _ctx: ExecutionContext): Promise<void> {
    const runId = newRunId();
    const logger = buildLogger(env, runId);
    const startedAt = Date.now();

    logger.info('run_started', {
      cron: controller.cron,
      scheduled_time_utc: new Date(controller.scheduledTime).toISOString(),
      api_version: env.SHOPIFY_API_VERSION || SHOPIFY_API_VERSION,
    });

    const creds = requireCredentials(env, logger);
    if (!creds) {
      logger.error('run_aborted', { reason: 'missing_configuration' });
      return;
    }

    const client = new ShopifyClient({
      store: creds.store,
      accessToken: creds.token,
      apiVersion: env.SHOPIFY_API_VERSION || SHOPIFY_API_VERSION,
      logger,
    });

    const summary = await runCloser(env, { client, logger, now: () => Date.now() });

    logger.info('run_finished', {
      discovered: summary.discovered,
      states: summary.states,
      errors: summary.errors,
      duration_ms: Date.now() - startedAt,
    });
  },

  /**
   * Health check only.
   *
   * This endpoint cannot close anything: it does not construct a Shopify
   * client, does not read the access token, and ignores every query
   * parameter. Closing happens only on the cron path.
   */
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname !== '/health') {
      return new Response('Not found', { status: 404 });
    }
    return Response.json({
      status: 'ok',
      service: 'greenside-competition-closer',
      dry_run: isDryRun(env),
      api_version: env.SHOPIFY_API_VERSION || SHOPIFY_API_VERSION,
      config: resolveConfig(env),
    });
  },
};
