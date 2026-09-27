/**
 * Competitions without a skill question: custom.skill_mode = none.
 *
 * A no-question competition's entries are recorded as NOT_REQUIRED -- not a
 * judgement, a record that there was nothing to judge. They are eligible for
 * the draw and do not need UNJUDGED clearance to freeze. Every other freeze
 * blocker and allocation invariant must still hold exactly as before.
 */
import { createRequire } from 'node:module';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { beforeEach, describe, expect, test } from 'vitest';
import { claimLowest } from '../src/allocate';
import { buildCompetitionConfig, type RawCompetitionMetafields } from '../src/config';
import { converge } from '../src/converge';
import { freeze, freezeBlockers } from '../src/freeze';
import { allocationId } from '../src/idempotency';
import { buildPool } from '../src/pool';
import { processOrder, writeEvent } from '../src/process';
import { parseSkillMode, snapshotSkill } from '../src/skill';
import { readSnapshot } from '../src/snapshot';
import { EVENT_CONTEXT, NOW, TestD1, seedAllocation, seedCompetition } from './helpers';
import { ORDER, SHOP, deps, type OrderOpts } from './order-fixtures';

const noSkill: RawCompetitionMetafields = {
  productGid: 'gid://shopify/Product/900001',
  handle: 'win-a-putter',
  title: 'Win a TaylorMade Putter',
  productStatus: 'ACTIVE',
  entriesTotal: '100',
  entryPrefix: 'PUT',
  entryStartNumber: '1001',
  skillQuestion: null,
  skillAnswers: null,
  skillAnswerCorrect: null,
  skillMode: 'none',
};

describe('custom.skill_mode configuration', () => {
  test('none needs no question and no correct answer', () => {
    const result = buildCompetitionConfig(noSkill);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.config.skillMode).toBe('none');
  });

  test('unset or blank keeps the existing behaviour: required, and the correct answer is still demanded', () => {
    for (const skillMode of [undefined, null, '', '  ', 'required']) {
      const result = buildCompetitionConfig({ ...noSkill, skillMode });
      expect(result.ok).toBe(false);
      if (result.ok) continue;
      expect(result.problems.map((p) => p.code)).toContain('MISSING_CORRECT_ANSWER');
    }
    const withAnswer = buildCompetitionConfig({ ...noSkill, skillMode: null, skillQuestion: 'Q?', skillAnswerCorrect: 'Bunker' });
    expect(withAnswer.ok && withAnswer.config.skillMode).toBe('required');
  });

  test('any other value is refused, never guessed', () => {
    for (const skillMode of ['off', 'None ', 'NONE', 'false', 'no']) {
      const parsed = parseSkillMode(skillMode);
      // Only the exact word "none" (surrounding whitespace aside) turns the question off.
      if (skillMode.trim() === 'none') continue;
      expect(parsed.ok).toBe(false);
      const result = buildCompetitionConfig({ ...noSkill, skillMode });
      expect(result.ok).toBe(false);
      if (result.ok) continue;
      expect(result.problems.map((p) => p.code)).toContain('INVALID_SKILL_MODE');
    }
  });

  test('leftover skill metafields on a none competition are ignored, not judged', () => {
    const result = buildCompetitionConfig({ ...noSkill, skillQuestion: 'Q?', skillAnswers: '["A","A"]', skillAnswerCorrect: 'Z' });
    expect(result.ok).toBe(true);
  });
});

describe('the NOT_REQUIRED verdict', () => {
  test('a none competition records NOT_REQUIRED with no rule version, judged_at or correct answer', () => {
    const snap = snapshotSkill({ question: null, answer: null, correct: null, now: NOW, mode: 'none' });
    expect(snap).toEqual({
      question: null, answer: null, correctSnapshot: null, verdict: 'NOT_REQUIRED', ruleVersion: null, judgedAt: null,
    });
  });

  test('stray properties are kept but never judged', () => {
    const snap = snapshotSkill({ question: 'Q?', answer: 'Wrong', correct: 'Bunker', now: NOW, mode: 'none' });
    expect(snap).toMatchObject({ question: 'Q?', answer: 'Wrong', correctSnapshot: null, verdict: 'NOT_REQUIRED' });
  });

  test('the default mode is unchanged: required, judged as before', () => {
    expect(snapshotSkill({ question: 'Q?', answer: 'Bunker', correct: 'Bunker', now: NOW }).verdict).toBe('CORRECT');
    expect(snapshotSkill({ question: 'Q?', answer: null, correct: 'Bunker', now: NOW }).verdict).toBe('INCORRECT');
    expect(snapshotSkill({ question: 'Q?', answer: 'Bunker', correct: null, now: NOW }).verdict).toBe('UNJUDGED');
  });
});

describe('allocation for a skill_mode = none competition (the webhook path)', () => {
  let db: TestD1;
  let competitionId: string;
  const ALLOC = () => allocationId(SHOP, '1042', '55');

  beforeEach(async () => {
    db = new TestD1();
    competitionId = seedCompetition(db, { capacity: 20, correct: null }).competitionId;
    await buildPool(db, competitionId, { prefix: 'PUT', startNumber: 1001, capacity: 20, padWidth: 4 });
  });

  const run = (o: OrderOpts, runId: string, reason: 'ORDER_EDIT' | 'CANCELLED' = 'ORDER_EDIT') =>
    processOrder(deps(db, o, runId), ORDER, reason);

  test('a paid order with no skill properties is allocated, lowest first, and recorded NOT_REQUIRED', async () => {
    const out = await run({ quantity: 3, currentQuantity: 3, skillMode: 'none' }, 'r1');
    expect(out[0]).toMatchObject({ action: 'ALLOCATE', claimed: ['PUT1001', 'PUT1002', 'PUT1003'] });

    const row = db.query<Record<string, unknown>>(`SELECT * FROM allocation`)[0];
    expect(row).toMatchObject({
      allocation_id: await ALLOC(),
      skill_verdict: 'NOT_REQUIRED',
      skill_question: null,
      skill_answer: null,
      skill_answer_correct_snapshot: null,
      skill_rule_version: null,
      skill_judged_at: null,
      entry_route: 'online',
      status: 'ALLOCATED',
      held_count: 3,
    });
    const events = db.query<{ event_type: string }>(`SELECT event_type FROM entry_event`).map((e) => e.event_type);
    expect(events).toEqual(['ALLOCATED', 'ALLOCATED', 'ALLOCATED']);
  });

  test('stray skill properties on the line do not create a judgement or a skill event', async () => {
    await run({ quantity: 1, currentQuantity: 1, skillMode: 'none', skillProperties: true, answer: 'Wrong' }, 'r1');
    expect(db.query(`SELECT skill_verdict, skill_answer FROM allocation`)[0]).toEqual({ skill_verdict: 'NOT_REQUIRED', skill_answer: 'Wrong' });
    expect(db.query(`SELECT 1 FROM entry_event WHERE event_type IN ('INCORRECT_SKILL', 'UNJUDGED_SKILL')`)).toHaveLength(0);
  });

  test('a mistyped skill_mode fails safe: judged as a question competition, never NOT_REQUIRED', async () => {
    // The product still carries its correct answer; the line carries no answer.
    await run({ quantity: 1, currentQuantity: 1, skillMode: 'off', skillProperties: false }, 'r1');
    expect(db.query(`SELECT skill_verdict FROM allocation`)[0]).toEqual({ skill_verdict: 'INCORRECT' });
    expect(db.query(`SELECT 1 FROM entry_event WHERE event_type = 'INCORRECT_SKILL'`)).toHaveLength(1);
    // And the configuration itself is invalid, so CONFIG_INVALID blocks any freeze.
    const config = buildCompetitionConfig({ ...noSkill, skillMode: 'off', skillQuestion: 'Q?', skillAnswerCorrect: 'Bunker' });
    expect(config.ok).toBe(false);
  });

  test('duplicate deliveries converge once; a cancellation releases once; the verdict never changes', async () => {
    const o: OrderOpts = { quantity: 2, currentQuantity: 2, skillMode: 'none' };
    await run(o, 'create');
    await run(o, 'paid'); // the second delivery for the same order
    expect(db.query(`SELECT COUNT(*) AS n FROM entry_event`)[0]).toEqual({ n: 2 });

    await run({ ...o, currentQuantity: 0, cancelledAt: NOW }, 'cancel', 'CANCELLED');
    await run({ ...o, currentQuantity: 0, cancelledAt: NOW }, 'refund', 'CANCELLED');
    expect(db.query(`SELECT status FROM entry_number WHERE seq IN (1001, 1002) ORDER BY seq`)).toEqual([
      { status: 'AVAILABLE' }, { status: 'AVAILABLE' },
    ]);
    const counts = db.query<{ event_type: string; n: number }>(
      `SELECT event_type, COUNT(*) AS n FROM entry_event GROUP BY event_type ORDER BY event_type`,
    );
    expect(counts).toEqual([
      { event_type: 'ALLOCATED', n: 2 }, { event_type: 'RELEASED', n: 2 }, { event_type: 'RETURNED_TO_POOL', n: 2 },
    ]);
    expect(db.query(`SELECT skill_verdict, status FROM allocation`)[0]).toEqual({ skill_verdict: 'NOT_REQUIRED', status: 'RELEASED' });
  });
});

describe('freezing a skill_mode = none competition', () => {
  let db: TestD1;
  let competitionId: string;

  beforeEach(async () => {
    db = new TestD1();
    // No correct answer configured at all, as for a real no-question competition.
    competitionId = seedCompetition(db, { capacity: 10, correct: null }).competitionId;
    await buildPool(db, competitionId, { prefix: 'PUT', startNumber: 1001, capacity: 10, padWidth: 4 });
  });

  async function allocate(id: string, count: number, verdict: 'NOT_REQUIRED' | 'CORRECT' | 'INCORRECT' | 'UNJUDGED' = 'NOT_REQUIRED') {
    seedAllocation(db, { allocationId: id, competitionId, orderId: `o-${id}`, lineItemId: `l-${id}`, verdict });
    if (verdict === 'INCORRECT' || verdict === 'UNJUDGED') {
      await writeEvent(db, {
        occurredAt: NOW, competitionId, allocationId: id, orderId: `o-${id}`,
        eventType: verdict === 'INCORRECT' ? 'INCORRECT_SKILL' : 'UNJUDGED_SKILL', ...EVENT_CONTEXT,
      });
    }
    await converge({
      db, competitionId, competitionStatus: 'OPEN', allocationId: id, currentQuantity: count, entriesPerUnit: 1,
      now: NOW, reason: 'REFUND', orderId: `o-${id}`, lineItemId: `l-${id}`, customerRef: null, ...EVENT_CONTEXT,
    });
  }

  const blockers = (over: Partial<Parameters<typeof freezeBlockers>[0]> = {}) =>
    freezeBlockers({
      db, competitionId, competitionStatus: 'OPEN', expectedCapacity: 10, configValid: true, skillMode: 'none', ...over,
    }).then((bs) => bs.map((b) => b.code));

  test('NOT_REQUIRED entries freeze cleanly: no UNJUDGED blocker, eligible count correct', async () => {
    await allocate('a1', 2);
    await allocate('a2', 3);
    expect(await blockers()).toEqual([]);
    const result = await freeze({
      db, competitionId, competitionStatus: 'OPEN', expectedCapacity: 10, configValid: true, skillMode: 'none', now: NOW,
    });
    expect(result).toMatchObject({ ok: true, eligible: 5 });
    expect(db.query(`SELECT status FROM competition`)[0]).toEqual({ status: 'FROZEN' });
  });

  test('the snapshot includes NOT_REQUIRED entries, ascending, with nothing excluded', async () => {
    await allocate('a1', 2);
    await allocate('a2', 1);
    await freeze({ db, competitionId, competitionStatus: 'OPEN', expectedCapacity: 10, configValid: true, skillMode: 'none', now: NOW });
    const snap = await readSnapshot({ db, competitionId, competitionStatus: 'FROZEN', frozenAt: NOW });
    expect(snap.entries.map((e) => [e.entry_number, e.skill_verdict])).toEqual([
      ['PUT1001', 'NOT_REQUIRED'], ['PUT1002', 'NOT_REQUIRED'], ['PUT1003', 'NOT_REQUIRED'],
    ]);
    expect(snap.eligibleCount).toBe(3);
    expect(snap.exclusions).toEqual([]);
  });

  test('a number released after the freeze stays RELEASED and leaves the list', async () => {
    await allocate('a1', 2);
    await freeze({ db, competitionId, competitionStatus: 'OPEN', expectedCapacity: 10, configValid: true, skillMode: 'none', now: NOW });
    await converge({
      db, competitionId, competitionStatus: 'FROZEN', allocationId: 'a1', currentQuantity: 0, entriesPerUnit: 1,
      now: NOW, reason: 'REFUND', orderId: 'o-a1', lineItemId: 'l-a1', customerRef: null, ...EVENT_CONTEXT,
    });
    expect(db.query<{ status: string }>(`SELECT status FROM entry_number WHERE seq IN (1001, 1002)`).every((r) => r.status === 'RELEASED')).toBe(true);
    const attempt = await claimLowest({ db, competitionId, allocationId: 'x', count: 1, now: NOW, orderId: 'o', lineItemId: 'l', customerRef: null });
    expect(attempt.ok && attempt.claimed.map((c) => c.entry_number)).toEqual(['PUT1003']);
  });

  describe('every unrelated blocker still refuses the freeze', () => {
    test('an orphaned claim', async () => {
      await allocate('a1', 1);
      await claimLowest({ db, competitionId, allocationId: 'orphan', count: 2, now: NOW, orderId: 'o', lineItemId: 'l', customerRef: null });
      expect(await blockers()).toContain('ORPHANED_CLAIMS');
    });

    test('ledger drift', async () => {
      await allocate('a1', 2);
      db.sqlite.exec(`UPDATE allocation SET held_count = 5 WHERE allocation_id = 'a1'`);
      expect(await blockers()).toContain('ALLOCATION_LEDGER_DRIFT');
    });

    test('an audit gap', async () => {
      await allocate('a1', 2);
      db.sqlite.exec(`DELETE FROM entry_event WHERE id = (SELECT MIN(id) FROM entry_event WHERE event_type = 'ALLOCATED')`);
      expect(await blockers()).toContain('AUDIT_GAP');
    });

    test('an invalid configuration, a pool-size mismatch and a non-OPEN status', async () => {
      await allocate('a1', 1);
      expect(await blockers({ configValid: false })).toContain('CONFIG_INVALID');
      expect(await blockers({ expectedCapacity: 11 })).toContain('POOL_SIZE_MISMATCH');
      expect(await blockers({ competitionStatus: 'FROZEN' })).toContain('NOT_OPEN');
      const refused = await freeze({
        db, competitionId, competitionStatus: 'OPEN', expectedCapacity: 10, configValid: false, skillMode: 'none', now: NOW,
      });
      expect(refused.ok).toBe(false);
      expect(db.query(`SELECT status FROM competition`)[0]).toEqual({ status: 'OPEN' });
    });
  });

  describe('a mode changed while entries exist is refused, never silently resolved', () => {
    test('a none competition holding a judged entry', async () => {
      await allocate('a1', 1, 'NOT_REQUIRED');
      await allocate('a2', 1, 'CORRECT');
      expect(await blockers()).toEqual(['SKILL_MODE_MISMATCH']);
    });

    test('a none competition holding an UNJUDGED entry: mismatch, not the UNJUDGED blocker', async () => {
      await allocate('a1', 1, 'UNJUDGED');
      expect(await blockers()).toEqual(['SKILL_MODE_MISMATCH']);
    });

    test('a question competition holding a NOT_REQUIRED entry', async () => {
      await allocate('a1', 1, 'NOT_REQUIRED');
      expect(await blockers({ skillMode: 'required' })).toEqual(['SKILL_MODE_MISMATCH']);
      // Callers that predate skill_mode get the same answer: the default is 'required'.
      expect(await blockers({ skillMode: undefined })).toEqual(['SKILL_MODE_MISMATCH']);
    });

    test('a fully released entry holds nothing and does not count', async () => {
      await allocate('a1', 1, 'CORRECT');
      await converge({
        db, competitionId, competitionStatus: 'OPEN', allocationId: 'a1', currentQuantity: 0, entriesPerUnit: 1,
        now: NOW, reason: 'REFUND', orderId: 'o-a1', lineItemId: 'l-a1', customerRef: null, ...EVENT_CONTEXT,
      });
      await allocate('a2', 1, 'NOT_REQUIRED');
      expect(await blockers()).toEqual([]);
    });

    test('a question competition still refuses UNJUDGED entries, exactly as before', async () => {
      await allocate('a1', 1, 'UNJUDGED');
      expect(await blockers({ skillMode: 'required' })).toEqual(['UNJUDGED_ALLOCATIONS']);
    });
  });
});

describe('schema: migration 0005', () => {
  const nodeRequire = createRequire(import.meta.url);
  const { DatabaseSync } = nodeRequire('node:sqlite') as typeof import('node:sqlite');
  const MIGRATIONS = new URL('../migrations', import.meta.url).pathname;
  const files = readdirSync(MIGRATIONS).filter((f: string) => f.endsWith('.sql')).sort();

  const insert = (verdict: string, judged: boolean, id = 'a1', line = 'l1') => `
    INSERT INTO allocation (allocation_id, shop_domain, competition_id, order_id, order_name, order_created_at,
      line_item_id, entry_route, ordered_quantity, entries_per_unit, target_count, held_count,
      skill_verdict, skill_judged_at, skill_rule_version, unit_price_minor, line_total_minor,
      decision_basis, status, source, mirror_state, created_at, updated_at)
    VALUES ('${id}', 's', 'c', 'o1', '#1', '${NOW}', '${line}', 'online', 1, 1, 1, 1,
      '${verdict}', ${judged ? `'${NOW}'` : 'NULL'}, ${judged ? `'v1'` : 'NULL'}, 100, 100,
      '{}', 'ALLOCATED', 'webhook', 'PENDING', '${NOW}', '${NOW}')`;

  test('NOT_REQUIRED is accepted without a rule version; the judged-verdict guard still holds', () => {
    const db = new TestD1();
    db.sqlite.exec(insert('NOT_REQUIRED', false));
    expect(() => db.sqlite.exec(insert('CORRECT', false, 'a2', 'l2'))).toThrow();
    expect(() => db.sqlite.exec(insert('SKIPPED', false, 'a3', 'l3'))).toThrow();
    expect(() => db.sqlite.exec(insert('NOT_REQUIRED', false, 'a4', 'l1'))).toThrow(); // UNIQUE (order_id, line_item_id)
  });

  test('existing rows survive the rebuild unchanged, and the indexes are recreated', () => {
    const db = new DatabaseSync(':memory:');
    db.exec('PRAGMA foreign_keys = ON');
    for (const f of files.filter((f: string) => f < '0005')) db.exec(readFileSync(join(MIGRATIONS, f), 'utf8'));
    db.exec(insert('CORRECT', true, 'a1', 'l1'));
    db.exec(insert('UNJUDGED', false, 'a2', 'l2'));
    const before = db.prepare(`SELECT * FROM allocation ORDER BY allocation_id`).all();

    for (const f of files.filter((f: string) => f >= '0005')) db.exec(readFileSync(join(MIGRATIONS, f), 'utf8'));

    expect(db.prepare(`SELECT * FROM allocation ORDER BY allocation_id`).all()).toEqual(before);
    const indexes = db.prepare(`SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'allocation' AND name LIKE 'ix_%' ORDER BY name`).all();
    expect(indexes.map((i) => (i as { name: string }).name)).toEqual(['ix_allocation_competition', 'ix_allocation_mirror', 'ix_allocation_order']);
    expect(db.prepare(`SELECT COUNT(*) AS n FROM sqlite_master WHERE name = 'allocation_new'`).get()).toEqual({ n: 0 });
  });
});
