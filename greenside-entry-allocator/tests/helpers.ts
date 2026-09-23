/**
 * Test harness.
 *
 * The D1 stub is backed by REAL SQLite (node:sqlite), not a fake. That matters:
 * the allocator's most important guarantees are database constraints --
 * ux_entry_number_global, the CHECKs on prefix and status, and the atomicity
 * of CLAIM_LOWEST. A hand-written fake would assert the code's opinion of
 * those rules rather than the rules themselves.
 */

import { createRequire } from 'node:module';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { Logger } from '../src/logging';
import type { D1Like, D1StatementLike } from '../src/db';

// Loaded through createRequire so the bundler does not try to resolve
// node:sqlite statically; it is a Node builtin, not a package.
const nodeRequire = createRequire(import.meta.url);
const { DatabaseSync } = nodeRequire('node:sqlite') as typeof import('node:sqlite');

const MIGRATIONS_DIR = new URL('../migrations', import.meta.url).pathname;

type Db = InstanceType<typeof DatabaseSync>;

class SqliteStatement implements D1StatementLike {
  private values: unknown[] = [];
  constructor(private readonly db: Db, private readonly sql: string) {}

  bind(...values: unknown[]): D1StatementLike {
    this.values = values.map((v) => (v === undefined ? null : v));
    return this;
  }

  async all<T = Record<string, unknown>>(): Promise<{ results: T[] }> {
    const stmt = this.db.prepare(this.sql);
    return { results: stmt.all(...(this.values as never[])) as T[] };
  }

  async first<T = Record<string, unknown>>(): Promise<T | null> {
    const stmt = this.db.prepare(this.sql);
    const rows = stmt.all(...(this.values as never[])) as T[];
    return rows.length > 0 ? (rows[0] as T) : null;
  }

  async run(): Promise<unknown> {
    const stmt = this.db.prepare(this.sql);
    return stmt.run(...(this.values as never[]));
  }
}

export class TestD1 implements D1Like {
  readonly sqlite: Db;

  constructor() {
    this.sqlite = new DatabaseSync(':memory:');
    this.sqlite.exec('PRAGMA foreign_keys = ON');
    for (const file of readdirSync(MIGRATIONS_DIR).filter((f: string) => f.endsWith('.sql')).sort()) {
      this.sqlite.exec(readFileSync(join(MIGRATIONS_DIR, file), 'utf8'));
    }
  }

  prepare(sql: string): D1StatementLike {
    return new SqliteStatement(this.sqlite, sql);
  }

  /** D1's batch is an implicit transaction. Mirrored here with a real one. */
  async batch(statements: D1StatementLike[]): Promise<unknown[]> {
    this.sqlite.exec('BEGIN');
    try {
      const out: unknown[] = [];
      for (const s of statements) out.push(await s.run());
      this.sqlite.exec('COMMIT');
      return out;
    } catch (err) {
      this.sqlite.exec('ROLLBACK');
      throw err;
    }
  }

  async exec(sql: string): Promise<unknown> {
    return this.sqlite.exec(sql);
  }

  /** Convenience for assertions. */
  query<T = Record<string, unknown>>(sql: string, ...params: unknown[]): T[] {
    return this.sqlite.prepare(sql).all(...(params as never[])) as T[];
  }
}

export function silentLogger(): Logger {
  return new Logger({}, [], () => {});
}

export const NOW = '2026-09-23T10:00:00.000Z';

/** Insert a competition directly, bypassing Shopify. */
export function seedCompetition(
  db: TestD1,
  overrides: Partial<{
    competition_id: string;
    prefix: string;
    start_number: number;
    capacity: number;
    pad_width: number;
    status: string;
    correct: string | null;
  }> = {},
): { competitionId: string; prefix: string; startNumber: number; capacity: number; padWidth: number } {
  const competitionId = overrides.competition_id ?? '900001';
  const prefix = overrides.prefix ?? 'PUT';
  const startNumber = overrides.start_number ?? 1001;
  const capacity = overrides.capacity ?? 100;
  const padWidth = overrides.pad_width ?? 4;
  const status = overrides.status ?? 'OPEN';
  const correct = overrides.correct === undefined ? 'Bunker' : overrides.correct;

  db.sqlite
    .prepare(
      `INSERT INTO competition (
         competition_id, shop_domain, product_gid, prefix, start_number, capacity, pad_width,
         handle_snapshot, title_snapshot, skill_question_snapshot, skill_answers_snapshot,
         skill_answer_correct_snapshot, status, created_at, updated_at, pool_built_at
       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    )
    .run(
      competitionId,
      'test.myshopify.com',
      `gid://shopify/Product/${competitionId}`,
      prefix,
      startNumber,
      capacity,
      padWidth,
      'test-competition',
      'Test competition',
      'What is a sand pit traditionally called in golf?',
      JSON.stringify(['Rough', 'Bunker', 'Fairway']),
      correct,
      status,
      NOW,
      NOW,
      NOW,
    );

  return { competitionId, prefix, startNumber, capacity, padWidth };
}

/** Insert an allocation ledger row directly. */
export function seedAllocation(
  db: TestD1,
  args: {
    allocationId: string;
    competitionId: string;
    orderId: string;
    lineItemId: string;
    verdict?: 'CORRECT' | 'INCORRECT' | 'UNJUDGED';
    entriesPerUnit?: number;
    orderedQuantity?: number;
  },
): void {
  const verdict = args.verdict ?? 'CORRECT';
  const judged = verdict !== 'UNJUDGED';
  db.sqlite
    .prepare(
      `INSERT INTO allocation (
         allocation_id, shop_domain, competition_id, order_id, order_name, order_created_at,
         line_item_id, variant_id, customer_ref, entry_route, ordered_quantity, entries_per_unit,
         target_count, held_count, skill_question, skill_answer, skill_answer_correct_snapshot,
         skill_verdict, skill_judged_at, skill_rule_version, unit_price_minor, line_total_minor,
         decision_basis, status, source, mirror_state, created_at, updated_at
       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    )
    .run(
      args.allocationId,
      'test.myshopify.com',
      args.competitionId,
      args.orderId,
      `#${args.orderId}`,
      NOW,
      args.lineItemId,
      null,
      null,
      'online',
      args.orderedQuantity ?? 1,
      args.entriesPerUnit ?? 1,
      0,
      0,
      'Q',
      'Bunker',
      'Bunker',
      verdict,
      judged ? NOW : null,
      judged ? 'v1' : null,
      249,
      249,
      '{}',
      'ALLOCATED',
      'test',
      'PENDING',
      NOW,
      NOW,
    );
}
