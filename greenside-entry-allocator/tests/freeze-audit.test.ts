/**
 * Freeze audit: AUDIT_GAP and ALLOCATION_LEDGER_DRIFT.
 *
 * Convergence is atomic, so none of these states should arise in operation.
 * They are manufactured here by editing rows directly, to prove freeze refuses
 * rather than finalises an entry list whose history does not add up.
 */
import { beforeEach, describe, expect, test } from 'vitest';
import { claimLowest } from '../src/allocate';
import { converge } from '../src/converge';
import { freezeBlockers } from '../src/freeze';
import { buildPool } from '../src/pool';
import { writeEvent } from '../src/process';
import { EVENT_CONTEXT, NOW, TestD1, seedAllocation, seedCompetition } from './helpers';

let db: TestD1;
const competitionId = '900001';

beforeEach(async () => {
  db = new TestD1();
  seedCompetition(db, { capacity: 10 });
  await buildPool(db, competitionId, { prefix: 'PUT', startNumber: 1001, capacity: 10, padWidth: 4 });
});

async function hold(allocationId: string, count: number, status = 'OPEN') {
  await converge({
    db, competitionId, competitionStatus: status, allocationId, currentQuantity: count, entriesPerUnit: 1,
    now: NOW, reason: 'REFUND', orderId: `o-${allocationId}`, lineItemId: `l-${allocationId}`, customerRef: null, ...EVENT_CONTEXT,
  });
}

async function codes(status = 'OPEN'): Promise<string[]> {
  const blockers = await freezeBlockers({ db, competitionId, competitionStatus: status, expectedCapacity: 10, configValid: true });
  return blockers.map((b) => b.code);
}

/** A history with every event kind: A holds 2 of 4, B was reissued a released number. */
async function busyHistory() {
  seedAllocation(db, { allocationId: 'A', competitionId, orderId: 'o-A', lineItemId: 'l-A' });
  seedAllocation(db, { allocationId: 'B', competitionId, orderId: 'o-B', lineItemId: 'l-B' });
  await hold('A', 4); // PUT1001-1004
  await hold('A', 2); // releases 1004, 1003 back to the pool
  await hold('B', 1); // reissues PUT1003 (issue #2)
}

describe('AUDIT_GAP', () => {
  test('a complete history, including a reissued number, passes', async () => {
    await busyHistory();
    expect(await codes()).toEqual([]);
  });

  test('a FROZEN release (RELEASED, never RETURNED_TO_POOL) is a complete history', async () => {
    seedAllocation(db, { allocationId: 'A', competitionId, orderId: 'o-A', lineItemId: 'l-A' });
    await hold('A', 3);
    db.sqlite.exec(`UPDATE competition SET status = 'FROZEN', frozen_at = '${NOW}'`);
    await hold('A', 1, 'FROZEN');
    expect(await codes('FROZEN')).not.toContain('AUDIT_GAP');
  });

  test.each([
    ['a missing ALLOCATED event', `DELETE FROM entry_event WHERE event_type = 'ALLOCATED' AND seq = 1001`],
    ['a missing ALLOCATED event for an earlier issue', `DELETE FROM entry_event WHERE event_type = 'ALLOCATED' AND seq = 1003 AND allocation_seq = 1`],
    ['a missing RELEASED event', `DELETE FROM entry_event WHERE event_type = 'RELEASED' AND seq = 1004`],
    ['a missing RETURNED_TO_POOL event', `DELETE FROM entry_event WHERE event_type = 'RETURNED_TO_POOL' AND seq = 1004`],
    ['a duplicated ALLOCATED event', `INSERT INTO entry_event (occurred_at, competition_id, seq, entry_number, allocation_id, allocation_seq,
       event_type, actor, run_id, detail_json)
     SELECT occurred_at, competition_id, seq, entry_number, allocation_id, allocation_seq, event_type, actor, run_id, detail_json
       FROM entry_event WHERE event_type = 'ALLOCATED' AND seq = 1002`],
    ['an ALLOCATED event naming the wrong holder', `UPDATE entry_event SET allocation_id = 'B' WHERE event_type = 'ALLOCATED' AND seq = 1001`],
  ])('%s blocks the freeze', async (_name, corruption) => {
    await busyHistory();
    db.sqlite.exec(corruption);
    expect(await codes()).toContain('AUDIT_GAP');
  });

  test('numbers claimed with no events at all block the freeze', async () => {
    seedAllocation(db, { allocationId: 'A', competitionId, orderId: 'o-A', lineItemId: 'l-A' });
    // claimLowest is the bare claim, with no events: exactly what the old crash left behind.
    await claimLowest({ db, competitionId, allocationId: 'A', count: 2, now: NOW, orderId: 'o-A', lineItemId: 'l-A', customerRef: null });
    db.sqlite.exec(`UPDATE allocation SET held_count = 2`);
    const blockers = await freezeBlockers({ db, competitionId, competitionStatus: 'OPEN', expectedCapacity: 10, configValid: true });
    const gap = blockers.find((b) => b.code === 'AUDIT_GAP');
    expect(gap?.detail).toContain('2 number(s)');
    expect(gap?.detail).toContain('PUT1001, PUT1002');
  });

  test('an INCORRECT allocation without its INCORRECT_SKILL event blocks the freeze', async () => {
    seedAllocation(db, { allocationId: 'A', competitionId, orderId: 'o-A', lineItemId: 'l-A', verdict: 'INCORRECT' });
    await hold('A', 1);
    expect(await codes()).toContain('AUDIT_GAP');

    await writeEvent(db, { occurredAt: NOW, competitionId, allocationId: 'A', eventType: 'INCORRECT_SKILL', ...EVENT_CONTEXT });
    expect(await codes()).not.toContain('AUDIT_GAP');
  });

  test('RETURNED_TO_POOL rows without allocation_seq still count', async () => {
    await busyHistory();
    db.sqlite.exec(`UPDATE entry_event SET allocation_seq = NULL WHERE event_type = 'RETURNED_TO_POOL'`);
    expect(await codes()).toEqual([]);
  });
});

describe('ALLOCATION_LEDGER_DRIFT', () => {
  test('a held_count that disagrees with the pool blocks the freeze', async () => {
    await busyHistory();
    expect(await codes()).not.toContain('ALLOCATION_LEDGER_DRIFT');
    db.sqlite.exec(`UPDATE allocation SET held_count = held_count + 1 WHERE allocation_id = 'A'`);
    expect(await codes()).toContain('ALLOCATION_LEDGER_DRIFT');
  });
});
