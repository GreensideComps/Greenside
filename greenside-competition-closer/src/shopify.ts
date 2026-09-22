/**
 * Shopify Admin GraphQL client.
 *
 * Deliberately tiny: one POST, bounded retries, and a strict split between
 * transient failures (retry) and permanent ones (do not retry). Nothing in
 * here decides whether a competition may be closed -- see eligibility.ts.
 */

import type { Logger } from './logging';

/**
 * Pinned, not "latest".
 *
 * 2026-07 is the newest STABLE Admin API version (2026-10 is a release
 * candidate at the time of writing). Shopify supports each stable version for
 * a minimum of 12 months. Pinning means a quarterly Shopify release can never
 * silently change the shape of an operation that zeroes inventory; upgrading
 * is a deliberate, tested act.
 */
export const SHOPIFY_API_VERSION = '2026-07';

/** Inventory quantity name we read and set. Shopify accepts 'available' or 'on_hand'. */
export const QUANTITY_NAME = 'available';

export type ErrorKind =
  | 'NETWORK'
  | 'HTTP'
  | 'THROTTLED'
  | 'GRAPHQL'
  | 'AUTH'
  | 'MALFORMED_RESPONSE';

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

export interface ProductVariantNode {
  id: string;
  inventoryItem: {
    id: string | null;
    inventoryLevel: {
      id: string;
      quantities: Array<{ name: string; quantity: number }>;
    } | null;
  } | null;
}

export interface ProductNode {
  id: string;
  title: string;
  status: string;
  tags: string[];
  variantsCount: { count: number } | null;
  closingAt: { value: string | null; type: string } | null;
  variants: { nodes: ProductVariantNode[] };
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

const CANDIDATES_QUERY = `
query CompetitionCandidates($cursor: String, $search: String!, $location: ID!) {
  products(first: 50, after: $cursor, query: $search) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      title
      status
      tags
      variantsCount { count }
      closingAt: metafield(namespace: "custom", key: "closing_at") { value type }
      variants(first: 2) {
        nodes {
          id
          inventoryItem {
            id
            inventoryLevel(locationId: $location) {
              id
              quantities(names: ["available"]) { name quantity }
            }
          }
        }
      }
    }
  }
}`;

const INVENTORY_READ_QUERY = `
query InventoryLevelRead($inventoryItemId: ID!, $location: ID!) {
  inventoryItem(id: $inventoryItemId) {
    id
    inventoryLevel(locationId: $location) {
      id
      quantities(names: ["available"]) { name quantity }
    }
  }
}`;

/**
 * The @idempotent directive is REQUIRED on this mutation as of API version
 * 2026-04. Shopify tracks the key for 24 hours and replays the original
 * response for a duplicate, which is what makes an overlapping cron safe.
 */
const SET_ZERO_MUTATION = `
mutation CloseCompetitionInventory($input: InventorySetQuantitiesInput!, $idempotencyKey: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    inventoryAdjustmentGroup {
      id
      createdAt
      reason
      referenceDocumentUri
      changes { name delta quantityAfterChange }
    }
    userErrors { field code message }
  }
}`;

const TAGS_ADD_MUTATION = `
mutation AddClosedTag($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}`;

interface CandidatesResponse {
  products: {
    pageInfo: { hasNextPage: boolean; endCursor: string | null };
    nodes: ProductNode[];
  };
}

export interface SetQuantityResult {
  adjustmentGroupId: string | null;
  quantityAfterChange: number | null;
  userErrors: Array<{ field?: string[] | null; code?: string | null; message: string }>;
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
        const e = err instanceof ShopifyError
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
    if (response.status === 429) {
      throw new ShopifyError('rate limited', 'THROTTLED', true, 429);
    }
    if (response.status >= 500) {
      throw new ShopifyError(`upstream ${response.status}`, 'HTTP', true, response.status);
    }
    if (!response.ok) {
      throw new ShopifyError(`unexpected status ${response.status}`, 'HTTP', false, response.status);
    }

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
    if (!body.data) {
      throw new ShopifyError('response contained no data', 'MALFORMED_RESPONSE', false, response.status);
    }
    return body.data;
  }

  /**
   * All products matching the search, following every page.
   *
   * The search string must NOT attempt to filter on the closing_at metafield:
   * Shopify silently ignores an unsupported metafield predicate and returns
   * the unfiltered set, which would look like a working filter while closing
   * competitions that are still open. The date comparison happens in
   * eligibility.ts instead.
   */
  async fetchCandidates(search: string, locationId: string, pageLimit = 40): Promise<ProductNode[]> {
    const out: ProductNode[] = [];
    let cursor: string | null = null;

    for (let page = 0; page < pageLimit; page++) {
      // The response type is named rather than inline: inferring it here made
      // `data` and `cursor` circular, since cursor is read from the response
      // and also fed back in as a variable.
      const data: CandidatesResponse = await this.request<CandidatesResponse>(
        CANDIDATES_QUERY,
        { cursor, search, location: locationId },
      );

      out.push(...data.products.nodes);
      if (!data.products.pageInfo.hasNextPage) return out;
      cursor = data.products.pageInfo.endCursor;
      if (!cursor) return out;
    }
    throw new ShopifyError(`pagination exceeded ${pageLimit} pages`, 'GRAPHQL', false);
  }

  /** Fresh read of available quantity, used for verification after a write. */
  async readAvailable(inventoryItemId: string, locationId: string): Promise<number | null> {
    const data = await this.request<{
      inventoryItem: { inventoryLevel: { quantities: Array<{ name: string; quantity: number }> } | null } | null;
    }>(INVENTORY_READ_QUERY, { inventoryItemId, location: locationId });

    const q = data.inventoryItem?.inventoryLevel?.quantities?.find((x) => x.name === QUANTITY_NAME);
    return q ? q.quantity : null;
  }

  async setAvailableQuantity(args: {
    inventoryItemId: string;
    locationId: string;
    quantity: number;
    changeFromQuantity: number;
    referenceDocumentUri: string;
    idempotencyKey: string;
  }): Promise<SetQuantityResult> {
    const data = await this.request<{
      inventorySetQuantities: {
        inventoryAdjustmentGroup: { id: string; changes: Array<{ name: string; quantityAfterChange: number | null }> } | null;
        userErrors: Array<{ field?: string[] | null; code?: string | null; message: string }>;
      };
    }>(SET_ZERO_MUTATION, {
      idempotencyKey: args.idempotencyKey,
      input: {
        name: QUANTITY_NAME,
        reason: 'correction',
        referenceDocumentUri: args.referenceDocumentUri,
        quantities: [
          {
            inventoryItemId: args.inventoryItemId,
            locationId: args.locationId,
            quantity: args.quantity,
            changeFromQuantity: args.changeFromQuantity,
          },
        ],
      },
    });

    const payload = data.inventorySetQuantities;
    const change = payload.inventoryAdjustmentGroup?.changes?.find((c) => c.name === QUANTITY_NAME);
    return {
      adjustmentGroupId: payload.inventoryAdjustmentGroup?.id ?? null,
      quantityAfterChange: change?.quantityAfterChange ?? null,
      userErrors: payload.userErrors ?? [],
    };
  }

  async addTags(productId: string, tags: string[]): Promise<Array<{ message: string }>> {
    const data = await this.request<{ tagsAdd: { userErrors: Array<{ message: string }> } }>(
      TAGS_ADD_MUTATION,
      { id: productId, tags },
    );
    return data.tagsAdd.userErrors ?? [];
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
