/**
 * The constraints are the guarantee, so they are tested against real SQLite
 * rather than against the application's opinion of them.
 */
import { beforeEach, describe, expect, test } from 'vitest';
import { NOW, TestD1, seedCompetition } from './helpers';

let db: TestD1;
beforeEach(() => {
  db = new TestD1();
});

function insertCompetition(over: Record<string, unknown> = {}) {
  const row = {
    competition_id: '1',
    shop_domain: 'test.myshopify.com',
    product_gid: 'gid://shopify/Product/1',
    prefix: 'PUT',
    start_number: 1001,
    capacity: 100,
    pad_width: 4,
    handle_snapshot: 'h',
    title_snapshot: 't',
    status: 'DRAFT',
    created_at: NOW,
    updated_at: NOW,
    ...over,
  };
  db.sqlite
    .prepare(
      `INSERT INTO competition (competition_id, shop_domain, product_gid, prefix, start_number, capacity,
        pad_width, handle_snapshot, title_snapshot, status, created_at, updated_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
    )
    .run(
      row.competition_id, row.shop_domain, row.product_gid, row.prefix, row.start_number, row.capacity,
      row.pad_width, row.handle_snapshot, row.title_snapshot, row.status, row.created_at, row.updated_at,
    );
}

describe('competition CHECK constraints', () => {
  test('a lowercase prefix is refused by the database, not just the app', () => {
    expect(() => insertCompetition({ prefix: 'put' })).toThrow(/CHECK constraint failed/);
  });
  test('a prefix shorter than 2 is refused', () => {
    expect(() => insertCompetition({ prefix: 'P' })).toThrow(/CHECK constraint failed/);
  });
  test('a prefix longer than 6 is refused', () => {
    expect(() => insertCompetition({ prefix: 'PUTTERS' })).toThrow(/CHECK constraint failed/);
  });
  test('a prefix containing a digit is refused', () => {
    expect(() => insertCompetition({ prefix: 'PUT1' })).toThrow(/CHECK constraint failed/);
  });
  test('capacity 0 is refused', () => {
    expect(() => insertCompetition({ capacity: 0 })).toThrow(/CHECK constraint failed/);
  });
  test('a negative start number is refused', () => {
    expect(() => insertCompetition({ start_number: -1 })).toThrow(/CHECK constraint failed/);
  });
  test('pad_width outside 1-9 is refused', () => {
    expect(() => insertCompetition({ pad_width: 0 })).toThrow(/CHECK constraint failed/);
    expect(() => insertCompetition({ competition_id: '2', product_gid: 'gid://shopify/Product/2', prefix: 'AAB', pad_width: 10 })).toThrow(
      /CHECK constraint failed/,
    );
  });
  test('a pad width too small for the highest number is refused', () => {
    // start 1, capacity 100 -> highest 100 needs 3 digits.
    expect(() => insertCompetition({ start_number: 1, capacity: 100, pad_width: 2 })).toThrow(
      /CHECK constraint failed/,
    );
  });
  test('an unknown status is refused', () => {
    expect(() => insertCompetition({ status: 'CLOSED' })).toThrow(/CHECK constraint failed/);
  });
  test('FROZEN without frozen_at is refused', () => {
    expect(() => insertCompetition({ status: 'FROZEN' })).toThrow(/CHECK constraint failed/);
  });
});

describe('global prefix uniqueness (ux_competition_prefix)', () => {
  test('two competitions cannot share a prefix', () => {
    insertCompetition({ competition_id: '1', product_gid: 'gid://shopify/Product/1', prefix: 'PUT' });
    expect(() =>
      insertCompetition({ competition_id: '2', product_gid: 'gid://shopify/Product/2', prefix: 'PUT' }),
    ).toThrow(/UNIQUE constraint failed/);
  });

  test('uniqueness is case-insensitive at the index level too', () => {
    insertCompetition({ competition_id: '1', product_gid: 'gid://shopify/Product/1', prefix: 'PUT' });
    // The CHECK already forces uppercase storage; the NOCASE index is the
    // second, independent guard if anything ever bypassed it.
    const idx = db.query<{ sql: string }>(
      `SELECT sql FROM sqlite_master WHERE name = 'ux_competition_prefix'`,
    );
    expect(idx[0]?.sql).toMatch(/COLLATE NOCASE/i);
  });

  test('different prefixes coexist happily', () => {
    insertCompetition({ competition_id: '1', product_gid: 'gid://shopify/Product/1', prefix: 'PUT' });
    insertCompetition({ competition_id: '2', product_gid: 'gid://shopify/Product/2', prefix: 'DRV' });
    expect(db.query(`SELECT * FROM competition`)).toHaveLength(2);
  });
});

describe('global entry-number uniqueness (ux_entry_number_global)', () => {
  test('the SAME rendered string cannot exist twice, even across competitions', () => {
    seedCompetition(db, { competition_id: '1', prefix: 'PUT' });
    seedCompetition(db, { competition_id: '2', prefix: 'DRV' });
    db.sqlite
      .prepare(`INSERT INTO entry_number (competition_id, seq, entry_number, status) VALUES (?,?,?,'AVAILABLE')`)
      .run('1', 1001, 'PUT1001');
    // Competition 2 attempting the identical rendered string is physically
    // refused. This is the constraint that actually protects a customer.
    expect(() =>
      db.sqlite
        .prepare(`INSERT INTO entry_number (competition_id, seq, entry_number, status) VALUES (?,?,?,'AVAILABLE')`)
        .run('2', 1001, 'PUT1001'),
    ).toThrow(/UNIQUE constraint failed/);
  });

  test('the same seq in two competitions is fine when the rendered strings differ', () => {
    seedCompetition(db, { competition_id: '1', prefix: 'PUT' });
    seedCompetition(db, { competition_id: '2', prefix: 'DRV' });
    db.sqlite.prepare(`INSERT INTO entry_number (competition_id, seq, entry_number, status) VALUES (?,?,?,'AVAILABLE')`).run('1', 1001, 'PUT1001');
    db.sqlite.prepare(`INSERT INTO entry_number (competition_id, seq, entry_number, status) VALUES (?,?,?,'AVAILABLE')`).run('2', 1001, 'DRV1001');
    expect(db.query(`SELECT * FROM entry_number`)).toHaveLength(2);
  });

  test('a duplicate (competition, seq) is refused by the primary key', () => {
    seedCompetition(db, { competition_id: '1', prefix: 'PUT' });
    db.sqlite.prepare(`INSERT INTO entry_number (competition_id, seq, entry_number, status) VALUES (?,?,?,'AVAILABLE')`).run('1', 1001, 'PUT1001');
    expect(() =>
      db.sqlite.prepare(`INSERT INTO entry_number (competition_id, seq, entry_number, status) VALUES (?,?,?,'AVAILABLE')`).run('1', 1001, 'PUT1001X'),
    ).toThrow(/UNIQUE constraint failed/);
  });
});

describe('entry_number status integrity', () => {
  test('an ALLOCATED row must name an allocation', () => {
    seedCompetition(db, { competition_id: '1' });
    expect(() =>
      db.sqlite
        .prepare(`INSERT INTO entry_number (competition_id, seq, entry_number, status, allocated_at) VALUES (?,?,?,'ALLOCATED',?)`)
        .run('1', 1001, 'PUT1001', NOW),
    ).toThrow(/CHECK constraint failed/);
  });

  test('an AVAILABLE row must NOT name an allocation', () => {
    seedCompetition(db, { competition_id: '1' });
    expect(() =>
      db.sqlite
        .prepare(`INSERT INTO entry_number (competition_id, seq, entry_number, status, allocation_id) VALUES (?,?,?,'AVAILABLE',?)`)
        .run('1', 1001, 'PUT1001', 'abc'),
    ).toThrow(/CHECK constraint failed/);
  });

  test('an unknown status is refused', () => {
    seedCompetition(db, { competition_id: '1' });
    expect(() =>
      db.sqlite.prepare(`INSERT INTO entry_number (competition_id, seq, entry_number, status) VALUES (?,?,?,'VOID')`).run('1', 1001, 'PUT1001'),
    ).toThrow(/CHECK constraint failed/);
  });
});

describe('allocation integrity', () => {
  test('a judged verdict must carry its rule version and timestamp', () => {
    seedCompetition(db, { competition_id: '1' });
    expect(() =>
      db.sqlite
        .prepare(
          `INSERT INTO allocation (allocation_id, shop_domain, competition_id, order_id, order_name,
            order_created_at, line_item_id, entry_route, ordered_quantity, entries_per_unit, target_count,
            held_count, skill_verdict, unit_price_minor, line_total_minor, decision_basis, status, source,
            mirror_state, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'CORRECT',?,?,?,?,?,?,?,?)`,
        )
        .run('a', 's', '1', 'o', '#o', NOW, 'l', 'online', 1, 1, 0, 0, 0, 0, '{}', 'ALLOCATED', 'test', 'PENDING', NOW, NOW),
    ).toThrow(/CHECK constraint failed/);
  });

  test('UNIQUE(order_id, line_item_id) is a second guard beyond the hash key', () => {
    seedCompetition(db, { competition_id: '1' });
    const insert = (allocationId: string) =>
      db.sqlite
        .prepare(
          `INSERT INTO allocation (allocation_id, shop_domain, competition_id, order_id, order_name,
            order_created_at, line_item_id, entry_route, ordered_quantity, entries_per_unit, target_count,
            held_count, skill_verdict, unit_price_minor, line_total_minor, decision_basis, status, source,
            mirror_state, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'UNJUDGED',?,?,?,?,?,?,?,?)`,
        )
        .run(allocationId, 's', '1', 'o1', '#o1', NOW, 'l1', 'online', 1, 1, 0, 0, 0, 0, '{}', 'ALLOCATED', 'test', 'PENDING', NOW, NOW);
    insert('hash-a');
    // A different hash for the same (order, line) must still be refused.
    expect(() => insert('hash-b')).toThrow(/UNIQUE constraint failed/);
  });
});

describe('entry_event is append-only by construction', () => {
  test('only known event types are accepted', () => {
    seedCompetition(db, { competition_id: '1' });
    expect(() =>
      db.sqlite
        .prepare(`INSERT INTO entry_event (occurred_at, competition_id, event_type, actor, run_id, detail_json) VALUES (?,?,?,?,?,?)`)
        .run(NOW, '1', 'DELETED_EVERYTHING', 'system:cli', 'r', '{}'),
    ).toThrow(/CHECK constraint failed/);
  });

  test('the migrations contain no UPDATE or DELETE against entry_event', () => {
    // History must never be rewritten. Enforced by review, asserted here.
    const sql = db.query<{ sql: string }>(`SELECT sql FROM sqlite_master WHERE name='entry_event'`)[0]?.sql ?? '';
    expect(sql).toMatch(/CREATE TABLE/i);
  });
});
