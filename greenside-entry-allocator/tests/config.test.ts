import { describe, expect, test } from 'vitest';
import {
  buildCompetitionConfig,
  entriesPerUnit,
  numericId,
  parseAnswerList,
  type RawCompetitionMetafields,
} from '../src/config';

const valid: RawCompetitionMetafields = {
  productGid: 'gid://shopify/Product/900001',
  handle: 'win-a-putter',
  title: 'Win a TaylorMade Putter',
  productStatus: 'ACTIVE',
  entriesTotal: '100',
  entryPrefix: 'PUT',
  entryStartNumber: '1001',
  skillQuestion: 'What is a sand pit traditionally called in golf?',
  skillAnswers: JSON.stringify(['Rough', 'Bunker', 'Fairway']),
  skillAnswerCorrect: 'Bunker',
};

function codes(raw: RawCompetitionMetafields) {
  const result = buildCompetitionConfig(raw);
  return result.ok ? [] : result.problems.map((p) => p.code);
}

describe('a complete configuration', () => {
  test('builds, and capacity comes from entries_total — never inferred', () => {
    const result = buildCompetitionConfig(valid);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.config.numbering).toEqual({ prefix: 'PUT', startNumber: 1001, capacity: 100, padWidth: 4 });
    expect(result.config.competitionId).toBe('900001');
  });

  test('canonicalises a lowercase prefix', () => {
    const result = buildCompetitionConfig({ ...valid, entryPrefix: ' put ' });
    expect(result.ok && result.config.numbering.prefix).toBe('PUT');
  });

  test('derives pad width from the highest number', () => {
    const result = buildCompetitionConfig({ ...valid, entryStartNumber: '1', entriesTotal: '100' });
    expect(result.ok && result.config.numbering.padWidth).toBe(3); // highest 100
  });
});

describe('refusals', () => {
  test('missing prefix', () => expect(codes({ ...valid, entryPrefix: null })).toContain('MISSING_PREFIX'));
  test('blank prefix', () => expect(codes({ ...valid, entryPrefix: '   ' })).toContain('MISSING_PREFIX'));
  test('invalid prefix', () => expect(codes({ ...valid, entryPrefix: 'P7' })).toContain('INVALID_NUMBERING'));
  test('missing start number', () =>
    expect(codes({ ...valid, entryStartNumber: null })).toContain('MISSING_START_NUMBER'));
  test('non-integer start number', () =>
    expect(codes({ ...valid, entryStartNumber: '10.5' })).toContain('MISSING_START_NUMBER'));
  test('junk start number', () =>
    expect(codes({ ...valid, entryStartNumber: '1001abc' })).toContain('MISSING_START_NUMBER'));
  test('missing capacity', () => expect(codes({ ...valid, entriesTotal: null })).toContain('MISSING_CAPACITY'));
  test('zero capacity', () => expect(codes({ ...valid, entriesTotal: '0' })).toContain('INVALID_NUMBERING'));
  test('negative capacity', () => expect(codes({ ...valid, entriesTotal: '-5' })).toContain('INVALID_NUMBERING'));
  test('missing correct answer', () =>
    expect(codes({ ...valid, skillAnswerCorrect: null })).toContain('MISSING_CORRECT_ANSWER'));
  test('correct answer not among the options', () =>
    expect(codes({ ...valid, skillAnswerCorrect: 'Sand trap' })).toContain('CORRECT_NOT_AN_OPTION'));
  test('duplicate options', () =>
    expect(codes({ ...valid, skillAnswers: JSON.stringify(['Bunker', 'bunker!']) })).toContain('DUPLICATE_OPTIONS'));

  test('free-text mode is valid with no options', () => {
    const result = buildCompetitionConfig({ ...valid, skillAnswers: null });
    expect(result.ok).toBe(true);
  });

  test('several problems are reported together', () => {
    const result = buildCompetitionConfig({
      ...valid,
      entryPrefix: null,
      entriesTotal: null,
      skillAnswerCorrect: null,
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.problems.length).toBeGreaterThanOrEqual(3);
  });
});

describe('helpers', () => {
  test('numericId extracts the tail of a gid', () => {
    expect(numericId('gid://shopify/Product/900001')).toBe('900001');
    expect(numericId('900001')).toBe('900001');
  });

  test('parseAnswerList handles a JSON array, null, blank and malformed input', () => {
    expect(parseAnswerList(JSON.stringify(['A', 'B']))).toEqual(['A', 'B']);
    expect(parseAnswerList(null)).toEqual([]);
    expect(parseAnswerList('   ')).toEqual([]);
    expect(parseAnswerList('not json')).toEqual([]);
    expect(parseAnswerList('{"a":1}')).toEqual([]);
    expect(parseAnswerList(JSON.stringify(['A', 3, null]))).toEqual(['A']);
  });

  test('entriesPerUnit mirrors the storefront: default 1, floor 1', () => {
    // Must match snippets/entry-selector.liquid, or an entrant is shown one
    // number of entries and issued another.
    expect(entriesPerUnit(null)).toBe(1);
    expect(entriesPerUnit('')).toBe(1);
    expect(entriesPerUnit('0')).toBe(1);
    expect(entriesPerUnit('-4')).toBe(1);
    expect(entriesPerUnit('junk')).toBe(1);
    expect(entriesPerUnit('5')).toBe(5);
    expect(entriesPerUnit('25')).toBe(25);
  });
});
