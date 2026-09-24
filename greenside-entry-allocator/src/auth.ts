/**
 * Shopify Admin API access tokens, by the client-credentials grant.
 *
 * The allocator's Shopify app is a Dev Dashboard app in the same organisation
 * as the store. Such an app has no permanent token to copy: it exchanges its
 * client ID and secret for an offline access token, and that token always
 * expires after 24 hours (`expires_in` 86399) with no refresh token. Renewal
 * is simply another exchange.
 *
 * So the Worker holds the client credentials, never a token. A token lives
 * only in this isolate's memory: it is fetched on first use, reused while
 * valid, replaced a few minutes before it expires, and dropped when Shopify
 * rejects it. It is never written to D1, KV, a log line or a secret.
 *
 * Before a token is used, the scopes Shopify reports for it are checked. A
 * token carrying write_inventory is refused outright: the allocator must never
 * be able to move stock, whatever the app configuration says.
 */

/** Replace a cached token this long before it expires. */
export const TOKEN_REFRESH_MARGIN_MS = 5 * 60 * 1000;

/** Scopes that make the allocator refuse to operate. */
export const FORBIDDEN_SCOPES = ['write_inventory'] as const;

/** Scopes the allocator's queries need. A write_X scope implies read_X. */
export const REQUIRED_SCOPES = ['read_orders', 'read_products'] as const;

export type TokenErrorCode = 'MISSING_CREDENTIALS' | 'EXCHANGE_FAILED' | 'FORBIDDEN_SCOPE' | 'MISSING_SCOPE';

/**
 * A token could not be obtained. Never retried here: the webhook fails with
 * 500 and Shopify redelivers it. The message never carries a credential.
 */
export class TokenError extends Error {
  constructor(
    message: string,
    readonly code: TokenErrorCode,
    readonly status?: number,
  ) {
    super(message);
    this.name = 'TokenError';
  }
}

/** Where ShopifyClient gets its access token. */
export interface TokenSource {
  getToken(): Promise<string>;
  /**
   * Drop `rejected` if it is still the cached token, so the next getToken()
   * exchanges again. A token that has already been replaced is left alone.
   */
  invalidate(rejected: string): void;
}

export interface ClientCredentialsOptions {
  store: string;
  clientId: string | undefined;
  clientSecret: string | undefined;
  fetchImpl?: typeof fetch;
  /** Milliseconds since the epoch; overridden in tests. */
  now?: () => number;
  refreshMarginMs?: number;
}

interface CachedToken {
  token: string;
  expiresAt: number;
}

export class ClientCredentialsTokenSource implements TokenSource {
  private readonly endpoint: string;
  private readonly fetchImpl: typeof fetch;
  private readonly now: () => number;
  private readonly refreshMarginMs: number;
  private cached: CachedToken | null = null;
  private inflight: Promise<CachedToken> | null = null;

  constructor(private readonly opts: ClientCredentialsOptions) {
    const host = opts.store.replace(/^https?:\/\//, '').replace(/\/+$/, '');
    this.endpoint = `https://${host}/admin/oauth/access_token`;
    this.fetchImpl = opts.fetchImpl ?? ((input, init) => fetch(input, init));
    this.now = opts.now ?? Date.now;
    this.refreshMarginMs = opts.refreshMarginMs ?? TOKEN_REFRESH_MARGIN_MS;
  }

  async getToken(): Promise<string> {
    if (this.cached && this.now() < this.cached.expiresAt - this.refreshMarginMs) return this.cached.token;

    // One exchange at a time: concurrent callers wait on the same request.
    if (!this.inflight) {
      this.inflight = this.exchange()
        .then((fresh) => {
          this.cached = fresh;
          return fresh;
        })
        .finally(() => {
          this.inflight = null;
        });
    }
    return (await this.inflight).token;
  }

  invalidate(rejected: string): void {
    if (this.cached?.token === rejected) this.cached = null;
  }

  private async exchange(): Promise<CachedToken> {
    const clientId = this.opts.clientId?.trim();
    const clientSecret = this.opts.clientSecret?.trim();
    if (!clientId || !clientSecret) {
      const missing = [!clientId ? 'SHOPIFY_CLIENT_ID' : null, !clientSecret ? 'SHOPIFY_CLIENT_SECRET' : null]
        .filter(Boolean)
        .join(', ');
      throw new TokenError(`client credentials missing: ${missing}`, 'MISSING_CREDENTIALS');
    }

    const requestedAt = this.now();
    let response: Response;
    try {
      response = await this.fetchImpl(this.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ client_id: clientId, client_secret: clientSecret, grant_type: 'client_credentials' }),
      });
    } catch (err) {
      throw new TokenError(`token exchange network failure: ${(err as Error)?.message ?? 'unknown'}`, 'EXCHANGE_FAILED');
    }

    let body: { access_token?: unknown; scope?: unknown; expires_in?: unknown; error?: unknown };
    try {
      body = (await response.json()) as typeof body;
    } catch {
      throw new TokenError(`token exchange returned non-JSON (HTTP ${response.status})`, 'EXCHANGE_FAILED', response.status);
    }

    if (!response.ok) {
      // Only Shopify's short error code is kept; nothing else from the body.
      const code = typeof body.error === 'string' ? body.error.replace(/[^a-z_]/gi, '').slice(0, 40) : 'unknown';
      throw new TokenError(`token exchange failed: HTTP ${response.status} ${code}`, 'EXCHANGE_FAILED', response.status);
    }

    const token = typeof body.access_token === 'string' ? body.access_token : '';
    const expiresIn = typeof body.expires_in === 'number' ? body.expires_in : NaN;
    if (token === '' || !(expiresIn > 0)) {
      throw new TokenError('token exchange response lacked access_token or expires_in', 'EXCHANGE_FAILED', response.status);
    }

    checkScopes(typeof body.scope === 'string' ? body.scope : '');
    return { token, expiresAt: requestedAt + expiresIn * 1000 };
  }
}

/** Refuse a token whose scopes are dangerous or insufficient. */
export function checkScopes(scope: string): void {
  const granted = new Set(
    scope
      .split(',')
      .map((s) => s.trim())
      .filter((s) => s !== ''),
  );
  const forbidden = FORBIDDEN_SCOPES.filter((s) => granted.has(s));
  if (forbidden.length > 0) {
    throw new TokenError(`refusing token: granted forbidden scope ${forbidden.join(', ')}`, 'FORBIDDEN_SCOPE');
  }
  const missing = REQUIRED_SCOPES.filter((s) => !granted.has(s) && !granted.has(s.replace(/^read_/, 'write_')));
  if (missing.length > 0) {
    throw new TokenError(`refusing token: missing scope ${missing.join(', ')}`, 'MISSING_SCOPE');
  }
}

/**
 * One token source per store and credential pair, kept for the life of the
 * isolate so the cached token outlives a single request. New credentials (a
 * rotated secret) get a new source; the old token is never reused for them.
 */
const sources = new Map<string, { clientSecret: string | undefined; source: ClientCredentialsTokenSource }>();

export function tokenSourceFor(opts: { store: string; clientId: string | undefined; clientSecret: string | undefined }): TokenSource {
  const key = `${opts.store}|${opts.clientId ?? ''}`;
  const existing = sources.get(key);
  if (existing && existing.clientSecret === opts.clientSecret) return existing.source;
  const source = new ClientCredentialsTokenSource(opts);
  sources.set(key, { clientSecret: opts.clientSecret, source });
  return source;
}

/** Tests only: forget every cached token source. */
export function resetTokenSources(): void {
  sources.clear();
}
