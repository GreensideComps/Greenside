/**
 * Structured JSON logging with secret scrubbing.
 *
 * Every line is a single JSON object so Cloudflare's log stream stays
 * machine-readable. Nothing here ever formats a secret: the scrubber is a
 * second line of defence for the case where a secret reaches a log field by
 * accident, for example inside an upstream error message that echoed a header.
 */

export type LogLevel = 'info' | 'warn' | 'error';

/** Field names whose values are never printed, whatever they contain. */
const REDACTED_KEYS = /^(.*_)?(token|secret|password|authorization|apikey|api_key)$/i;

export const REDACTED = '[REDACTED]';

export type LogFields = Record<string, unknown>;

/**
 * NOTE ON PROVENANCE
 *
 * This file is copied verbatim from greenside-competition-closer/src/logging.ts
 * apart from this block: the closer's `FinalState` union was its own domain
 * vocabulary (CLOSED, VERIFICATION_FAILED, ...) and would be dead, misleading
 * code here. The allocator's event vocabulary lives in src/events.ts instead.
 *
 * Nothing else is changed. The Logger, the redaction rules and newRunId are
 * the closer's tested implementation.
 */

function redactKeys(value: unknown, depth = 0): unknown {
  if (depth > 6 || value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map((v) => redactKeys(v, depth + 1));
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    out[k] = REDACTED_KEYS.test(k) ? REDACTED : redactKeys(v, depth + 1);
  }
  return out;
}

export class Logger {
  private readonly secrets: string[];

  constructor(
    private readonly base: LogFields = {},
    secrets: Array<string | undefined> = [],
    private readonly sink: (line: string) => void = (line) => console.log(line),
  ) {
    // Short strings are not distinctive enough to scrub safely: replacing a
    // 3-character "secret" would corrupt unrelated output.
    this.secrets = secrets.filter((s): s is string => typeof s === 'string' && s.length >= 8);
  }

  child(extra: LogFields): Logger {
    return new Logger({ ...this.base, ...extra }, this.secrets, this.sink);
  }

  info(event: string, fields: LogFields = {}): void {
    this.emit('info', event, fields);
  }

  warn(event: string, fields: LogFields = {}): void {
    this.emit('warn', event, fields);
  }

  error(event: string, fields: LogFields = {}): void {
    this.emit('error', event, fields);
  }

  private emit(level: LogLevel, event: string, fields: LogFields): void {
    const record = {
      level,
      event,
      timestamp: new Date().toISOString(),
      ...this.base,
      ...(redactKeys(fields) as LogFields),
    };
    let line: string;
    try {
      line = JSON.stringify(record);
    } catch {
      line = JSON.stringify({ level: 'error', event: 'log_serialisation_failed', original_event: event });
    }
    this.sink(this.scrub(line));
  }

  /** Last-resort removal of any secret value that reached the serialised line. */
  private scrub(line: string): string {
    let out = line;
    for (const secret of this.secrets) {
      if (out.includes(secret)) out = out.split(secret).join(REDACTED);
    }
    return out;
  }
}

/** Run identifier. Random is fine: it groups log lines, it is not a key. */
export function newRunId(): string {
  return crypto.randomUUID();
}
