import { describe, expect, test } from 'vitest';
import {
  SKILL_RULE_VERSION,
  isFreeText,
  judge,
  normaliseAnswer,
  snapshotSkill,
  validateSkillConfig,
} from '../src/skill';

describe('normalisation — the four steps, in order', () => {
  test('1. trims', () => expect(normaliseAnswer('  Bunker  ')).toBe('bunker'));
  test('2. collapses internal whitespace', () => expect(normaliseAnswer('sand   trap')).toBe('sand trap'));
  test('   collapses tabs and newlines too', () => expect(normaliseAnswer('sand\t\ntrap')).toBe('sand trap'));
  test('3. casefolds', () => expect(normaliseAnswer('BuNkEr')).toBe('bunker'));
  test('4. strips surrounding punctuation', () => {
    expect(normaliseAnswer('"Bunker"')).toBe('bunker');
    expect(normaliseAnswer('Bunker!')).toBe('bunker');
    expect(normaliseAnswer('...Bunker...')).toBe('bunker');
    expect(normaliseAnswer('(Bunker)')).toBe('bunker');
  });

  test('does NOT strip punctuation from the inside of an answer', () => {
    expect(normaliseAnswer("it's a bunker")).toBe("it's a bunker");
    expect(normaliseAnswer('par-3')).toBe('par-3');
  });

  test('is idempotent', () => {
    const once = normaliseAnswer('  "Sand  Trap!"  ');
    expect(normaliseAnswer(once)).toBe(once);
  });

  test('an all-punctuation answer normalises to empty', () => {
    expect(normaliseAnswer('!!!')).toBe('');
  });
});

describe('normalisation — what it deliberately does NOT do', () => {
  // The skill question is what makes this a competition of skill rather than a
  // lottery. Judging must be a rule an entrant could apply themselves.
  test('no fuzzy matching: a typo is wrong', () => {
    expect(judge('bunkar', 'Bunker')).toBe('INCORRECT');
  });
  test('no edit distance: one character out is wrong', () => {
    expect(judge('bunke', 'Bunker')).toBe('INCORRECT');
  });
  test('no synonym inference: a real-world synonym is wrong', () => {
    expect(judge('sand trap', 'Bunker')).toBe('INCORRECT');
  });
  test('no substring matching', () => {
    expect(judge('the bunker over there', 'Bunker')).toBe('INCORRECT');
  });
});

describe('judging', () => {
  test('exact match after normalisation is CORRECT', () => {
    expect(judge('  BUNKER! ', 'Bunker')).toBe('CORRECT');
  });
  test('a different answer is INCORRECT', () => {
    expect(judge('Fairway', 'Bunker')).toBe('INCORRECT');
  });
  test('an empty entrant answer is INCORRECT, not UNJUDGED', () => {
    expect(judge('', 'Bunker')).toBe('INCORRECT');
    expect(judge(null, 'Bunker')).toBe('INCORRECT');
  });
  test('a missing correct answer is UNJUDGED — we cannot judge what we were not told', () => {
    expect(judge('Bunker', null)).toBe('UNJUDGED');
    expect(judge('Bunker', '   ')).toBe('UNJUDGED');
  });
  test('is deterministic across repeated calls', () => {
    for (let i = 0; i < 50; i++) expect(judge('Bunker', 'bunker')).toBe('CORRECT');
  });
});

describe('configuration validation', () => {
  test('multiple choice: the correct answer must be one of the options', () => {
    const problems = validateSkillConfig({ answers: ['Rough', 'Bunker', 'Fairway'], correct: 'Sand trap' });
    expect(problems.map((p) => p.code)).toContain('CORRECT_NOT_AN_OPTION');
  });

  test('multiple choice: a correct answer that IS an option passes', () => {
    expect(validateSkillConfig({ answers: ['Rough', 'Bunker', 'Fairway'], correct: 'Bunker' })).toEqual([]);
  });

  test('multiple choice: matching is normalised, so case and spacing do not matter', () => {
    expect(validateSkillConfig({ answers: ['Rough', 'Bunker'], correct: '  bunker ' })).toEqual([]);
  });

  test('free text: no options is valid, and the option check is skipped', () => {
    expect(validateSkillConfig({ answers: [], correct: 'Bunker' })).toEqual([]);
  });

  test('a missing correct answer is always a problem', () => {
    expect(validateSkillConfig({ answers: ['A', 'B'], correct: null }).map((p) => p.code)).toContain(
      'MISSING_CORRECT_ANSWER',
    );
    expect(validateSkillConfig({ answers: [], correct: '  ' }).map((p) => p.code)).toContain('MISSING_CORRECT_ANSWER');
  });

  test('options that normalise identically are rejected as ambiguous', () => {
    const problems = validateSkillConfig({ answers: ['Bunker', ' bunker! '], correct: 'Bunker' });
    expect(problems.map((p) => p.code)).toContain('DUPLICATE_OPTIONS');
  });

  test('isFreeText reflects whether options were configured', () => {
    expect(isFreeText({ answers: [], correct: 'x' })).toBe(true);
    expect(isFreeText({ answers: ['   '], correct: 'x' })).toBe(true);
    expect(isFreeText({ answers: ['A'], correct: 'A' })).toBe(false);
  });
});

describe('the allocation-time snapshot', () => {
  const NOW = '2026-09-23T10:00:00.000Z';

  test('captures question, answer, correct answer, verdict and rule version', () => {
    const snap = snapshotSkill({ question: 'Q?', answer: 'Bunker', correct: 'Bunker', now: NOW });
    expect(snap).toEqual({
      question: 'Q?',
      answer: 'Bunker',
      correctSnapshot: 'Bunker',
      verdict: 'CORRECT',
      ruleVersion: SKILL_RULE_VERSION,
      judgedAt: NOW,
    });
  });

  test('an UNJUDGED snapshot carries no rule version or timestamp', () => {
    const snap = snapshotSkill({ question: 'Q?', answer: 'Bunker', correct: null, now: NOW });
    expect(snap.verdict).toBe('UNJUDGED');
    expect(snap.ruleVersion).toBeNull();
    expect(snap.judgedAt).toBeNull();
  });

  test('the verdict follows the SNAPSHOT, so a later metafield edit cannot re-judge it', () => {
    // Entry sold while the correct answer was 'Bunker'.
    const atSale = snapshotSkill({ question: 'Q?', answer: 'Bunker', correct: 'Bunker', now: NOW });
    expect(atSale.verdict).toBe('CORRECT');
    // The merchant later changes the metafield to 'Fairway'. Re-judging the
    // stored snapshot must still give CORRECT, because the snapshot is what
    // governs -- not a fresh read.
    expect(judge(atSale.answer, atSale.correctSnapshot)).toBe('CORRECT');
    expect(judge(atSale.answer, 'Fairway')).toBe('INCORRECT'); // what a fresh read would wrongly say
  });

  test('the rule version is pinned to v1', () => {
    expect(SKILL_RULE_VERSION).toBe('v1');
  });
});
