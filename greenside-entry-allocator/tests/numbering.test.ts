import { describe, expect, test } from 'vitest';
import {
  canonicalPrefix,
  defaultPadWidth,
  isInRange,
  lastSeq,
  rangesCollide,
  renderEntryNumber,
  renderRange,
  validateNumbering,
  type NumberingConfig,
} from '../src/numbering';

const PUT: NumberingConfig = { prefix: 'PUT', startNumber: 1001, capacity: 100, padWidth: 4 };
const DRV: NumberingConfig = { prefix: 'DRV', startNumber: 5001, capacity: 250, padWidth: 4 };

describe('the specified range', () => {
  test('PUT/1001/100 renders exactly PUT1001..PUT1100 and exactly 100 numbers', () => {
    const rendered = renderRange(PUT);
    // Both endpoints inclusive: 1100 - 1001 + 1 = 100. An off-by-one here
    // would issue 99 or 101 entries against a published cap of 100.
    expect(rendered).toHaveLength(100);
    expect(rendered[0]).toBe('PUT1001');
    expect(rendered[99]).toBe('PUT1100');
    expect(lastSeq(PUT)).toBe(1100);
  });

  test('the sequence is contiguous and ascending with no gaps', () => {
    const rendered = renderRange(PUT);
    for (let i = 0; i < rendered.length; i++) {
      expect(rendered[i]).toBe(`PUT${1001 + i}`);
    }
    expect(new Set(rendered).size).toBe(100);
  });

  test('DRV/5001/250 renders DRV5001..DRV5250', () => {
    const rendered = renderRange(DRV);
    expect(rendered).toHaveLength(250);
    expect(rendered[0]).toBe('DRV5001');
    expect(rendered[249]).toBe('DRV5250');
  });

  test('nothing is hard-coded to PUT', () => {
    const arbitrary: NumberingConfig = { prefix: 'ZZQ', startNumber: 7, capacity: 3, padWidth: 2 };
    expect(renderRange(arbitrary)).toEqual(['ZZQ07', 'ZZQ08', 'ZZQ09']);
  });
});

describe('boundaries', () => {
  test('a capacity of 1 yields exactly one number', () => {
    expect(renderRange({ prefix: 'AA', startNumber: 1, capacity: 1, padWidth: 1 })).toEqual(['AA1']);
  });

  test('start_number 0 is permitted', () => {
    const cfg: NumberingConfig = { prefix: 'AA', startNumber: 0, capacity: 3, padWidth: 1 };
    expect(validateNumbering(cfg)).toEqual([]);
    expect(renderRange(cfg)).toEqual(['AA0', 'AA1', 'AA2']);
  });

  test('isInRange respects both inclusive endpoints', () => {
    expect(isInRange(PUT, 1001)).toBe(true);
    expect(isInRange(PUT, 1100)).toBe(true);
    expect(isInRange(PUT, 1000)).toBe(false);
    expect(isInRange(PUT, 1101)).toBe(false);
  });
});

describe('padding', () => {
  test('pads to the declared width', () => {
    expect(renderEntryNumber('AA', 7, 4)).toBe('AA0007');
    expect(renderEntryNumber('AA', 1234, 4)).toBe('AA1234');
  });

  test('defaultPadWidth sizes to the highest number, not the first', () => {
    expect(defaultPadWidth(1, 100)).toBe(3); // highest is 100
    expect(defaultPadWidth(1001, 100)).toBe(4); // highest is 1100
    expect(defaultPadWidth(5001, 250)).toBe(4); // highest is 5250
  });

  test('a pad width too small for the highest number is rejected', () => {
    // start 1, capacity 100 -> highest 100 needs 3 digits.
    const problems = validateNumbering({ prefix: 'AA', startNumber: 1, capacity: 100, padWidth: 2 });
    expect(problems.join(' ')).toMatch(/pad_width 2 is too small/);
  });

  test('padding keeps every number the same width, so they sort correctly', () => {
    const rendered = renderRange({ prefix: 'AA', startNumber: 1, capacity: 100, padWidth: 3 });
    expect(new Set(rendered.map((n) => n.length)).size).toBe(1);
    expect(rendered[0]).toBe('AA001');
    expect(rendered[99]).toBe('AA100');
    expect([...rendered].sort()).toEqual(rendered);
  });
});

describe('prefix rules', () => {
  test('canonicalPrefix uppercases and trims', () => {
    expect(canonicalPrefix('  put ')).toBe('PUT');
  });

  test.each([
    ['P', 'too short'],
    ['PUTTER1', 'too long'],
    ['PU7', 'contains a digit'],
    ['PU-T', 'contains punctuation'],
    ['put', 'lowercase'],
    ['', 'empty'],
  ])('rejects prefix %s (%s)', (prefix) => {
    const problems = validateNumbering({ prefix, startNumber: 1, capacity: 10, padWidth: 2 });
    expect(problems.some((p) => p.startsWith('prefix must be'))).toBe(true);
  });

  test.each(['AA', 'PUT', 'DRIVER'])('accepts prefix %s', (prefix) => {
    expect(validateNumbering({ prefix, startNumber: 1, capacity: 10, padWidth: 2 })).toEqual([]);
  });
});

describe('invalid configuration', () => {
  test('capacity must be at least 1', () => {
    expect(validateNumbering({ ...PUT, capacity: 0 }).join(' ')).toMatch(/capacity must be an integer >= 1/);
    expect(validateNumbering({ ...PUT, capacity: -5 }).join(' ')).toMatch(/capacity must be an integer >= 1/);
  });

  test('start_number must be a non-negative integer', () => {
    expect(validateNumbering({ ...PUT, startNumber: -1 }).join(' ')).toMatch(/start_number must be an integer >= 0/);
    expect(validateNumbering({ ...PUT, startNumber: 1.5 }).join(' ')).toMatch(/start_number must be an integer >= 0/);
  });

  test('pad_width must be 1-9', () => {
    expect(validateNumbering({ ...PUT, padWidth: 0 }).join(' ')).toMatch(/pad_width must be an integer 1-9/);
    expect(validateNumbering({ ...PUT, padWidth: 10 }).join(' ')).toMatch(/pad_width must be an integer 1-9/);
  });

  test('every problem is reported at once, not just the first', () => {
    const problems = validateNumbering({ prefix: 'x', startNumber: -1, capacity: 0, padWidth: 99 });
    expect(problems.length).toBeGreaterThanOrEqual(4);
  });
});

describe('range collision detection', () => {
  test('different prefixes never collide', () => {
    expect(rangesCollide(PUT, DRV)).toBe(false);
  });

  test('the same prefix with overlapping ranges collides', () => {
    expect(rangesCollide(PUT, { prefix: 'PUT', startNumber: 1050, capacity: 100, padWidth: 4 })).toBe(true);
  });

  test('the same prefix with adjacent, non-overlapping ranges does not collide', () => {
    expect(rangesCollide(PUT, { prefix: 'PUT', startNumber: 1101, capacity: 50, padWidth: 4 })).toBe(false);
  });

  test('touching at exactly one endpoint counts as a collision', () => {
    expect(rangesCollide(PUT, { prefix: 'PUT', startNumber: 1100, capacity: 5, padWidth: 4 })).toBe(true);
  });

  test('differing pad widths are compared on the rendered string', () => {
    const wide = { prefix: 'AA', startNumber: 1, capacity: 5, padWidth: 4 }; // AA0001..
    const narrow = { prefix: 'AA', startNumber: 1, capacity: 5, padWidth: 1 }; // AA1..
    expect(rangesCollide(wide, narrow)).toBe(false);
  });
});
