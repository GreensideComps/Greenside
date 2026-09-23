/**
 * Shopify Admin GraphQL client.
 *
 * PROVENANCE AND SCOPE
 *
 * The HTTP core below -- ShopifyError, the error taxonomy, request(), once()
 * and the bounded retry with jitter -- is copied from
 * greenside-competition-closer/src/shopify.ts, which is tested and proven.
 *
 * Everything else was STRIPPED. The closer's module also carried
 * setAvailableQuantity() and addTags(); importing it wholesale would have put
 * an inventory-write path inside this Worker's bundle and quietly destroyed
 * the guarantee that the allocator cannot move stock. Removed here:
 *
 *   - setAvailableQuantity()   inventory write
 *   - addTags()                product write
 *   - CANDIDATES_QUERY, INVENTORY_READ_QUERY, SET_ZERO_MUTATION,
 *     TAGS_ADD_MUTATION, QUANTITY_NAME and the closer's node types
 *
 * THE ONLY WRITE THIS CLIENT CAN PERFORM is setOrderEntryNumbersMetafield(),
 * the order-metafield mirror. There is no product write, no inventory write,
 * no discount write and no order-state write of any kind. The token this
 * Worker uses must come from an app holding only read_orders, read_all_orders,
 * read_products and write_orders.
 */

import type { Logger } from './logging';

/**
 * Pinned, not "latest". 2026-07 is the newest stable Admin API version.
 * Pinning means a quarterly Shopify release can never silently change the
 * shape of an operation that decides who is in a prize draw.
 */
export const SHOPIFY_API_VERSION = '2026-07';

export type ErrorKind = 'NETWORK' | 'HTTP' | 'THROTTLED' | 'GRAPHQL' | 'AUTH' | 'MALFORMED_RESPONSE';

export class ShopifyError extends Error {
  constructor(
    message: string,
    readonly kind: ErrorKind,
    readonly retryable: boolean,
    readonly status?: number,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = 'ShopifyError';
  }
}

export interface ShopifyClientOptions {
  store: string;
  accessToken: string;
  apiVersion?: string;
  logger: Logger;
  fetchImpl?: typeof fetch;
  /** Total attempts for a transient failure, including the first. */
  maxAttempts?: number;
  /** Base backoff in ms; overridden to 0 in tests. */
  backoffBaseMs?: number;
}

/* ------------------------------------------------------------------ *
 * Operations. Read-only, except the single order-metafield mirror.
 * ------------------------------------------------------------------ */

/**
 * The canonical order read.
 *
 * `quantity` is the immutable original; `currentQuantity` excludes refunded
 * AND removed units and is what convergence targets.
 *
 * `discountedTotalSet` takes withCodeDiscounts. WITHOUT that argument it
 * EXCLUDES code-based discounts: verified on order #1006, where the default
 * reported GBP 1.00 on a line whose order totalled GBP 0.00. Never read the
 * default form for money.
 */
export const ORDER_QUERY = `
query AllocatorOrder($id: ID!) {
  order(id: $id) {
    id
    name
    createdAt
    test
    cancelledAt
    displayFinancialStatus
    customer { id }
    lineItems(first: 50) {
      nodes {
        id
        quantity
        currentQuantity
        variant { id }
        product { id }
        discountedUnitPriceAfterAllDiscountsSet { shopMoney { amount } }
        discountedTotalSet(withCodeDiscounts: true) { shopMoney { amount } }
        customAttributes { key value }
      }
    }
  }
}`;

/** Competition configuration. Read-only. */
export const COMPETITION_QUERY = `
query AllocatorCompetition($id: ID!) {
  product(id: $id) {
    id
    handle
    title
    status
    entriesTotal:      metafield(namespace: "custom", key: "entries_total") { value }
    entryPrefix:       metafield(namespace: "custom", key: "entry_prefix") { value }
    entryStartNumber:  metafield(namespace: "custom", key: "entry_start_number") { value }
    skillQuestion:     metafield(namespace: "custom", key: "skill_question") { value }
    skillAnswers:      metafield(namespace: "custom", key: "skill_answers") { value }
    skillAnswerCorrect: metafield(namespace: "custom", key: "skill_answer_correct") { value }
    variants(first: 100) {
      nodes { id entries: metafield(namespace: "custom", key: "entries") { value } }
    }
  }
}`;

/** Reconciliation sweep. Read-only. */
export const ORDERS_SWEEP_QUERY = `
query AllocatorSweep($cursor: String, $search: String!) {
  orders(first: 50, after: $cursor, query: $search, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes { id updatedAt }
  }
}`;

/**
 * The ONLY write. Projects the D1 ledger onto the order so a human can see the
 * numbers in admin. D1 remains authoritative and this is never read back as
 * truth. The definition must be created with storefront access NONE.
 */
export const ORDER_MIRROR_MUTATION = `
mutation AllocatorMirror($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { id namespace key }
    userErrors { field message code }
  }
}`;

export interface OrderLineItemNode {
  id: string;
  quantity: number;
  currentQuantity: number;
  variant: { id: string } | null;
  product: { id: string } | null;
  discountedUnitPriceAfterAllDiscountsSet: { shopMoney: { amount: string } };
  discountedTotalSet: { shopMoney: { amount: string } };
  customAttributes: Array<{ key: string; value: string }>;
}

export interface OrderNode {
  id: string;
  name: string;
  createdAt: string;
  test: boolean;
  cancelledAt: string | null;
  displayFinancialStatus: string;
  customer: { id: string } | null;
  lineItems: { nodes: OrderLineItemNode[] };
}

export interface CompetitionProductNode {
  id: string;
  handle: string;
  title: string;
  status: string;
  entriesTotal: { value: string } | null;
  entryPrefix: { value: string } | null;
  entryStartNumber: { value: string } | null;
  skillQuestion: { value: string } | null;
  skillAnswers: { value: string } | null;
  skillAnswerCorrect: { value: string } | null;
  variants: { nodes: Array<{ id: string; entries: { value: string } | null }> };
}

export class ShopifyClient {
  private readonly endpoint: string;
  private readonly fetchImpl: typeof fetch;
  private readonly maxAttempts: number;
  private readonly backoffBaseMs: number;
  readonly apiVersion: string;

  constructor(private readonly opts: ShopifyClientOptions) {
    this.apiVersion = opts.apiVersion ?? SHOPIFY_API_VERSION;
    const host = opts.store.replace(/^https?:\/\//, '').replace(/\/+$/, '');
    this.endpoint = `https://${host}/admin/api/${this.apiVersion}/graphql.json`;
    this.fetchImpl = opts.fetchImpl ?? fetch;
    this.maxAttempts = opts.maxAttempts ?? 3;
    this.backoffBaseMs = opts.backoffBaseMs ?? 500;
  }

  /** One GraphQL round trip, with bounded retries for transient failures only. */
  async request<T>(query: string, variables: Record<string, unknown>): Promise<T> {
    let lastError: ShopifyError | undefined;

    for (let attempt = 1; attempt <= this.maxAttempts; attempt++) {
      try {
        return await this.once<T>(query, variables);
      } catch (err) {
        const e =
          err instanceof ShopifyError
            ? err
            : new ShopifyError(String((err as Error)?.message ?? err), 'NETWORK', true);
        lastError = e;
        if (!e.retryable || attempt === this.maxAttempts) throw e;
        this.opts.logger.warn('shopify_request_retry', {
          attempt,
          max_attempts: this.maxAttempts,
          error_kind: e.kind,
          status: e.status,
        });
        await sleep(this.backoffBaseMs * 2 ** (attempt - 1) + Math.floor(Math.random() * 100));
      }
    }
    throw lastError ?? new ShopifyError('request failed', 'NETWORK', false);
  }

  private async once<T>(query: string, variables: Record<string, unknown>): Promise<T> {
    let response: Response;
    try {
      response = await this.fetchImpl(this.endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Shopify-Access-Token': this.opts.accessToken,
          Accept: 'application/json',
        },
        body: JSON.stringify({ query, variables }),
      });
    } catch (err) {
      throw new ShopifyError(`network failure: ${(err as Error)?.message ?? 'unknown'}`, 'NETWORK', true);
    }

    if (response.status === 401 || response.status === 403) {
      // Never retried: a bad or under-scoped token will not fix itself, and
      // retrying burns the run. The message deliberately carries no headers.
      throw new ShopifyError('authentication or scope failure', 'AUTH', false, response.status);
    }
    if (response.status === 429) throw new ShopifyError('rate limited', 'THROTTLED', true, 429);
    if (response.status >= 500) throw new ShopifyError(`upstream ${response.status}`, 'HTTP', true, response.status);
    if (!response.ok) throw new ShopifyError(`unexpected status ${response.status}`, 'HTTP', false, response.status);

    let body: { data?: T; errors?: Array<{ message: string; extensions?: { code?: string } }> };
    try {
      body = (await response.json()) as typeof body;
    } catch {
      throw new ShopifyError('response was not valid JSON', 'MALFORMED_RESPONSE', false, response.status);
    }

    if (body.errors?.length) {
      const throttled = body.errors.some((e) => e.extensions?.code === 'THROTTLED');
      throw new ShopifyError(
        body.errors.map((e) => e.message).join('; '),
        throttled ? 'THROTTLED' : 'GRAPHQL',
        throttled,
        response.status,
        body.errors,
      );
    }
    if (!body.data) throw new ShopifyError('response contained no data', 'MALFORMED_RESPONSE', false, response.status);
    return body.data;
  }

  async fetchOrder(orderGid: string): Promise<OrderNode | null> {
    const data = await this.request<{ order: OrderNode | null }>(ORDER_QUERY, { id: orderGid });
    return data.order;
  }

  async fetchCompetitionProduct(productGid: string): Promise<CompetitionProductNode | null> {
    const data = await this.request<{ product: CompetitionProductNode | null }>(COMPETITION_QUERY, { id: productGid });
    return data.product;
  }

  /** The single permitted write. */
  async setOrderEntryNumbersMetafield(orderGid: string, valueJson: string): Promise<Array<{ message: string }>> {
    const data = await this.request<{
      metafieldsSet: { userErrors: Array<{ message: string }> };
    }>(ORDER_MIRROR_MUTATION, {
      metafields: [
        { ownerId: orderGid, namespace: 'greenside', key: 'entry_numbers', type: 'json', value: valueJson },
      ],
    });
    return data.metafieldsSet.userErrors ?? [];
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
