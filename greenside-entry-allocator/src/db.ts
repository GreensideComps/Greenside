/**
 * Every SQL statement the allocator runs, in one place.
 *
 * The statements are exported as strings so they can be read, reviewed and
 * tested against a real SQLite engine without standing up a Worker.
 *
 * THE CENTRAL STATEMENT IS CLAIM_LOWEST. Read its comment before changing
 * anything here.
 */

/** Minimal surface of a D1 database, so tests can supply a SQLite-backed stub. */
export interface D1Like {
  prepare(sql: string): D1StatementLike;
  batch(statements: D1StatementLike[]): Promise<unknown[]>;
  exec(sql: string): Promise<unknown>;
}

export interface D1StatementLike {
  bind(...values: unknown[]): D1StatementLike;
  all<T = Record<string, unknown>>(): Promise<{ results: T[] }>;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  run(): Promise<unknown>;
}

/* ------------------------------------------------------------------ *
 * Competition
 * ------------------------------------------------------------------ */

export const UPSERT_COMPETITION = `
INSERT INTO competition (
  competition_id, shop_domain, product_gid, prefix, start_number, capacity, pad_width,
  handle_snapshot, title_snapshot,
  skill_question_snapshot, skill_answers_snapshot, skill_answer_correct_snapshot,
  status, created_at, updated_at
) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, 'DRAFT', ?13, ?13)
ON CONFLICT (competition_id) DO UPDATE SET
  handle_snapshot = excluded.handle_snapshot,
  title_snapshot  = excluded.title_snapshot,
  skill_question_snapshot = excluded.skill_question_snapshot,
  skill_answers_snapshot  = excluded.skill_answers_snapshot,
  skill_answer_correct_snapshot = excluded.skill_answer_correct_snapshot,
  updated_at = excluded.updated_at
RETURNING *`;

export const SELECT_COMPETITION = `SELECT * FROM competition WHERE competition_id = ?1`;

export const SELECT_COMPETITION_BY_PREFIX = `
SELECT competition_id FROM competition WHERE prefix = ?1 COLLATE NOCASE`;

export const SET_COMPETITION_STATUS = `
UPDATE competition SET status = ?2, updated_at = ?3 WHERE competition_id = ?1 RETURNING *`;

export const MARK_POOL_BUILT = `
UPDATE competition SET status = 'OPEN', pool_built_at = ?2, updated_at = ?2
 WHERE competition_id = ?1 AND status = 'DRAFT'
RETURNING *`;

/**
 * OPEN -> FROZEN. Allocator-owned.
 *
 * Guarded in SQL as well as in freeze.ts so a concurrent second call cannot
 * double-freeze: the second UPDATE matches no row.
 */
export const FREEZE_COMPETITION = `
UPDATE competition SET status = 'FROZEN', frozen_at = ?2, updated_at = ?2
 WHERE competition_id = ?1 AND status = 'OPEN'
RETURNING *`;

/* ------------------------------------------------------------------ *
 * Pool
 * ------------------------------------------------------------------ */

export const INSERT_POOL_ROW = `
INSERT INTO entry_number (competition_id, seq, entry_number, status, allocation_seq)
VALUES (?1, ?2, ?3, 'AVAILABLE', 0)`;

export const COUNT_POOL = `
SELECT COUNT(*) AS n FROM entry_number WHERE competition_id = ?1`;

export const COUNT_POOL_BY_STATUS = `
SELECT status, COUNT(*) AS n FROM entry_number WHERE competition_id = ?1 GROUP BY status`;

export const MAX_POOL_SEQ = `
SELECT MAX(seq) AS max_seq FROM entry_number WHERE competition_id = ?1`;

/** Shrinking is refused when any number that would disappear is not free. */
export const COUNT_NON_AVAILABLE_ABOVE = `
SELECT COUNT(*) AS n FROM entry_number
 WHERE competition_id = ?1 AND seq > ?2 AND status <> 'AVAILABLE'`;

/* ------------------------------------------------------------------ *
 * Allocation -- THE CENTRAL STATEMENT
 * ------------------------------------------------------------------ */

/**
 * Claim the lowest N available numbers, atomically and all-or-nothing.
 *
 * Properties, in order of importance:
 *
 *  1. ATOMIC. One UPDATE is indivisible in SQLite, and D1 serialises writes to
 *     a single database, so two Workers cannot interleave inside it. There is
 *     no application-level lock to leak, time out or forget.
 *
 *  2. ALL-OR-NOTHING. The `(SELECT COUNT(*) ...) >= ?4` sub-predicate is false
 *     for EVERY row when fewer than N are free, so an under-capacity request
 *     updates zero rows instead of half-filling an order. The caller treats
 *     zero rows as REFUSED_CAPACITY.
 *
 *  3. LOWEST FIRST. `ORDER BY seq ASC LIMIT ?4` -- exactly the specified
 *     behaviour, including after a release has returned low numbers to the
 *     pool.
 *
 *  4. SELF-IDENTIFYING. allocation_id is stamped onto the rows by the SAME
 *     statement that claims them. If the process dies before the allocation
 *     ledger row is written, the claim is still attributable and therefore
 *     repairable -- the numbers are not lost.
 *
 *  5. It RETURNS exactly what it claimed, so there is no follow-up read that
 *     could race.
 */
export const CLAIM_LOWEST = `
UPDATE entry_number
   SET status         = 'ALLOCATED',
       allocation_id  = ?2,
       order_id       = ?5,
       line_item_id   = ?6,
       customer_ref   = ?7,
       allocation_seq = allocation_seq + 1,
       allocated_at   = ?3,
       released_at    = NULL,
       release_reason = NULL
 WHERE competition_id = ?1
   AND status = 'AVAILABLE'
   AND (SELECT COUNT(*) FROM entry_number p
         WHERE p.competition_id = ?1 AND p.status = 'AVAILABLE') >= ?4
   AND seq IN (SELECT p2.seq FROM entry_number p2
                WHERE p2.competition_id = ?1 AND p2.status = 'AVAILABLE'
                ORDER BY p2.seq ASC LIMIT ?4)
RETURNING entry_number, seq, allocation_seq`;

/**
 * Release the highest N numbers held by one allocation -- LIFO.
 *
 * Highest first keeps the entrant's retained block contiguous and low, and
 * makes a second partial refund behave identically to the first.
 *
 * `to_status` is passed in rather than assumed: while the competition is OPEN
 * a released number returns to AVAILABLE, but once FROZEN it stays RELEASED
 * for good. That rule lives in exactly one place, freeze-aware, in converge.ts.
 */
export const RELEASE_HIGHEST = `
UPDATE entry_number
   SET status         = ?5,
       allocation_id  = NULL,
       order_id       = NULL,
       line_item_id   = NULL,
       customer_ref   = NULL,
       released_at    = ?3,
       release_reason = ?6
 WHERE competition_id = ?1
   AND allocation_id = ?2
   AND status = 'ALLOCATED'
   AND seq IN (SELECT p.seq FROM entry_number p
                WHERE p.competition_id = ?1 AND p.allocation_id = ?2 AND p.status = 'ALLOCATED'
                ORDER BY p.seq DESC LIMIT ?4)
RETURNING entry_number, seq, allocation_seq`;

export const COUNT_HELD_BY_ALLOCATION = `
SELECT COUNT(*) AS n FROM entry_number
 WHERE competition_id = ?1 AND allocation_id = ?2 AND status = 'ALLOCATED'`;

export const SELECT_HELD_BY_ALLOCATION = `
SELECT entry_number, seq, allocation_seq FROM entry_number
 WHERE competition_id = ?1 AND allocation_id = ?2 AND status = 'ALLOCATED'
 ORDER BY seq ASC`;

export const COUNT_AVAILABLE = `
SELECT COUNT(*) AS n FROM entry_number WHERE competition_id = ?1 AND status = 'AVAILABLE'`;

/** Orphan sweep: rows claimed by an allocation_id that has no ledger row. */
export const SELECT_ORPHANED_CLAIMS = `
SELECT e.competition_id, e.allocation_id, COUNT(*) AS n
  FROM entry_number e
  LEFT JOIN allocation a ON a.allocation_id = e.allocation_id
 WHERE e.status = 'ALLOCATED' AND a.allocation_id IS NULL
 GROUP BY e.competition_id, e.allocation_id`;

/* ------------------------------------------------------------------ *
 * Allocation ledger
 * ------------------------------------------------------------------ */

export const INSERT_ALLOCATION = `
INSERT INTO allocation (
  allocation_id, shop_domain, competition_id,
  order_id, order_name, order_created_at, line_item_id, variant_id, customer_ref,
  entry_route, ordered_quantity, entries_per_unit, target_count, held_count,
  skill_question, skill_answer, skill_answer_correct_snapshot,
  skill_verdict, skill_judged_at, skill_rule_version,
  unit_price_minor, line_total_minor, decision_basis,
  status, source, mirror_state, created_at, updated_at
) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?18,?19,?20,?21,?22,?23,?24,?25,'PENDING',?26,?26)`;

export const SELECT_ALLOCATION = `SELECT * FROM allocation WHERE allocation_id = ?1`;

export const SELECT_ALLOCATION_BY_LINE = `
SELECT * FROM allocation WHERE order_id = ?1 AND line_item_id = ?2`;

export const UPDATE_ALLOCATION_COUNTS = `
UPDATE allocation SET target_count = ?2, held_count = ?3, status = ?4, updated_at = ?5
 WHERE allocation_id = ?1`;

export const SET_MIRROR_STATE = `
UPDATE allocation SET mirror_state = ?2, mirror_attempts = mirror_attempts + ?3, updated_at = ?4
 WHERE allocation_id = ?1`;

export const COUNT_UNJUDGED = `
SELECT COUNT(*) AS n FROM allocation
 WHERE competition_id = ?1 AND skill_verdict = 'UNJUDGED' AND status <> 'RELEASED'`;

export const SELECT_ALLOCATIONS_FOR_ORDER = `
SELECT * FROM allocation WHERE order_id = ?1`;

/* ------------------------------------------------------------------ *
 * Events -- append only. There is deliberately no UPDATE or DELETE here.
 * ------------------------------------------------------------------ */

export const INSERT_EVENT = `
INSERT INTO entry_event (
  occurred_at, competition_id, seq, entry_number, allocation_id, allocation_seq,
  event_type, from_status, to_status, order_id, customer_ref, reason,
  actor, run_id, webhook_id, detail_json
) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16)`;

export const SELECT_EVENTS_FOR_NUMBER = `
SELECT * FROM entry_event
 WHERE competition_id = ?1 AND seq = ?2
 ORDER BY id ASC`;

export const INSERT_WEBHOOK_DELIVERY = `
INSERT OR IGNORE INTO webhook_delivery (webhook_id, topic, received_at, outcome)
VALUES (?1, ?2, ?3, ?4)`;

/* ------------------------------------------------------------------ *
 * Frozen snapshot -- the ONLY draw-facing read.
 *
 * The allocator hands over a list. It does not hash it, does not choose a
 * winner and knows nothing about randomness. That is the draw project's work.
 * ------------------------------------------------------------------ */

export const SELECT_FROZEN_SNAPSHOT = `
SELECT e.entry_number, e.seq, e.allocation_id, e.allocation_seq,
       a.order_id, a.order_name, a.customer_ref, a.entry_route, a.skill_verdict
  FROM entry_number e
  JOIN allocation a ON a.allocation_id = e.allocation_id
 WHERE e.competition_id = ?1
   AND e.status = 'ALLOCATED'
   AND a.skill_verdict = 'CORRECT'
 ORDER BY e.seq ASC`;

/** Everything excluded from the snapshot, and why. Auditable alongside it. */
export const SELECT_SNAPSHOT_EXCLUSIONS = `
SELECT e.entry_number, e.seq, a.skill_verdict, a.order_id
  FROM entry_number e
  JOIN allocation a ON a.allocation_id = e.allocation_id
 WHERE e.competition_id = ?1
   AND e.status = 'ALLOCATED'
   AND a.skill_verdict <> 'CORRECT'
 ORDER BY e.seq ASC`;
