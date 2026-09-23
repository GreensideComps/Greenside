/**
 * Greenside entry allocator.
 *
 * Responsibility ends at:
 *   valid purchase -> allocation -> entry number -> refund/cancellation
 *   handling -> auditable, frozen-ready state.
 *
 * It does NOT draw. No randomness, no beacon, no winner selection, no
 * commitment hashing, no public proof, no redraw. Those belong to a separate
 * draw project, which consumes the read-only snapshot this Worker exposes.
 *
 * It also cannot move stock: there is no inventory write anywhere in this
 * bundle, and the token it uses must not hold write_inventory.
 */

import { Logger, newRunId } from './logging';
import { verifyWebhook } from './hmac';
import { ShopifyClient, SHOPIFY_API_VERSION } from './shopify';
import { processOrder, writeEvent } from './process';
import type { D1Like } from './db';
import type { ReleaseReason } from './events';

export interface Env {
  DB: D1Like;
  SHOPIFY_STORE?: string;
  SHOPIFY_ACCESS_TOKEN?: string;
  SHOPIFY_WEBHOOK_SECRET?: string;
  SHOPIFY_API_VERSION?: string;
  ALLOWED_SHOP_DOMAIN?: string;
  DRY_RUN?: string;
}

/**
 * Dry run is the default and can only be turned off by the exact string
 * "false". An unset, misspelled or empty variable leaves the Worker
 * read-only, so a configuration mistake can never produce a write.
 */
export function isDryRun(env: Env): boolean {
  return env.DRY_RUN !== 'false';
}

/** Which webhook topics this Worker accepts, and why each one runs. */
export const TOPIC_ROUTES: Record<string, { path: string; reason: ReleaseReason }> = {
  'orders/create': { path: '/webhooks/orders/create', reason: 'ORDER_EDIT' },
  'orders/paid': { path: '/webhooks/orders/paid', reason: 'ORDER_EDIT' },
  'orders/cancelled': { path: '/webhooks/orders/cancelled', reason: 'CANCELLED' },
  'orders/edited': { path: '/webhooks/orders/edited', reason: 'ORDER_EDIT' },
  'refunds/create': { path: '/webhooks/refunds/create', reason: 'REFUND' },
};

const PATH_TO_TOPIC: Record<string, { topic: string; reason: ReleaseReason }> = Object.fromEntries(
  Object.entries(TOPIC_ROUTES).map(([topic, route]) => [route.path, { topic, reason: route.reason }]),
);

/** Order id out of any supported webhook payload shape. */
export function orderGidFromPayload(topic: string, payload: Record<string, unknown>): string | null {
  // refunds/create carries the refund, whose order_id names the order.
  const raw = topic === 'refunds/create' ? payload['order_id'] : payload['id'];
  if (raw === undefined || raw === null) return null;
  const id = String(raw);
  return id.startsWith('gid://') ? id : `gid://shopify/Order/${id}`;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const runId = newRunId();
    const logger = new Logger({ run_id: runId, dry_run: isDryRun(env) }, [
      env.SHOPIFY_ACCESS_TOKEN,
      env.SHOPIFY_WEBHOOK_SECRET,
    ]);

    /**
     * Health check only. It constructs no Shopify client, reads no token and
     * ignores every query parameter, so it cannot allocate anything.
     */
    if (url.pathname === '/health') {
      return Response.json({
        status: 'ok',
        service: 'greenside-entry-allocator',
        dry_run: isDryRun(env),
        api_version: env.SHOPIFY_API_VERSION || SHOPIFY_API_VERSION,
        topics: Object.keys(TOPIC_ROUTES),
        draw_capability: 'none — the draw system is a separate project',
        inventory_write_capability: 'none',
      });
    }

    const route = PATH_TO_TOPIC[url.pathname];
    if (!route) return new Response('Not found', { status: 404 });
    if (request.method !== 'POST') return new Response('Method not allowed', { status: 405 });

    const secret = env.SHOPIFY_WEBHOOK_SECRET;
    const allowedShop = env.ALLOWED_SHOP_DOMAIN;
    if (!secret || !allowedShop) {
      logger.error('missing_configuration', {
        missing: [!secret ? 'SHOPIFY_WEBHOOK_SECRET' : null, !allowedShop ? 'ALLOWED_SHOP_DOMAIN' : null].filter(
          Boolean,
        ),
      });
      return new Response('Not configured', { status: 500 });
    }

    // RAW BODY, read once, verified before any parse. Re-serialising would
    // change the bytes and invalidate the signature.
    const rawBody = await request.text();
    const verification = await verifyWebhook({
      rawBody,
      headers: request.headers,
      secret,
      allowedShopDomain: allowedShop,
      expectedTopic: route.topic,
    });

    if (!verification.ok) {
      // Nothing from the body is logged. The rejection reason is enough.
      logger.warn('webhook_rejected', { reason: verification.rejection, path: url.pathname });
      return new Response('Unauthorized', { status: 401 });
    }

    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(rawBody) as Record<string, unknown>;
    } catch {
      // Authentic but malformed: retrying will not help, so do not ask Shopify to.
      logger.warn('webhook_unparseable', { topic: route.topic });
      return new Response('OK', { status: 200 });
    }

    const orderGid = orderGidFromPayload(route.topic, payload);
    if (!orderGid) {
      logger.warn('webhook_no_order_id', { topic: route.topic });
      return new Response('OK', { status: 200 });
    }

    if (isDryRun(env)) {
      logger.info('dry_run_webhook', { topic: route.topic, order_gid: orderGid, webhook_id: verification.webhookId });
      return Response.json({ status: 'dry_run', topic: route.topic, order: orderGid });
    }

    const store = env.SHOPIFY_STORE?.trim();
    const token = env.SHOPIFY_ACCESS_TOKEN?.trim();
    if (!store || !token) {
      logger.error('missing_configuration', {
        missing: [!store ? 'SHOPIFY_STORE' : null, !token ? 'SHOPIFY_ACCESS_TOKEN' : null].filter(Boolean),
      });
      return new Response('Not configured', { status: 500 });
    }

    const shopify = new ShopifyClient({
      store,
      accessToken: token,
      apiVersion: env.SHOPIFY_API_VERSION || SHOPIFY_API_VERSION,
      logger,
    });

    try {
      const outcomes = await processOrder(
        {
          db: env.DB,
          shopify,
          logger,
          shopDomain: allowedShop,
          runId,
          actor: 'system:webhook',
          now: () => new Date().toISOString(),
          webhookId: verification.webhookId,
        },
        orderGid,
        route.reason,
      );
      logger.info('webhook_processed', { topic: route.topic, order_gid: orderGid, lines: outcomes.length });
      return Response.json({ status: 'ok', outcomes });
    } catch (err) {
      // A genuine transient failure: 500 asks Shopify to retry, and the
      // reconciler is the backstop if every retry is exhausted.
      logger.error('webhook_failed', { topic: route.topic, error: String((err as Error)?.message ?? err) });
      return new Response('Processing failed', { status: 500 });
    }
  },

  async scheduled(controller: ScheduledController, env: Env): Promise<void> {
    const runId = newRunId();
    const logger = new Logger({ run_id: runId, dry_run: isDryRun(env) }, [
      env.SHOPIFY_ACCESS_TOKEN,
      env.SHOPIFY_WEBHOOK_SECRET,
    ]);
    logger.info('reconcile_started', {
      cron: controller.cron,
      scheduled_time_utc: new Date(controller.scheduledTime).toISOString(),
    });

    // Reconciliation is deliberately not wired to live Shopify in this stage:
    // no production webhooks are registered and no live sweep runs. The
    // mechanism is exercised by tests against fixtures.
    if (isDryRun(env)) {
      logger.info('reconcile_skipped', { reason: 'DRY_RUN is not the exact string "false"' });
      return;
    }
    logger.info('reconcile_finished', { note: 'sweep implementation lands with live webhook registration' });
  },
};

export { writeEvent };
