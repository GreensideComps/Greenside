/**
 * Skill-question judging.
 *
 * Pure functions only. Deterministic and reproducible: the same answer, the
 * same correct answer and the same rule version always give the same verdict,
 * forever. A verdict that cannot be re-derived cannot be defended.
 *
 * THERE IS NO FUZZY MATCHING HERE, AND THERE MUST NEVER BE.
 *
 * No edit distance, no synonym inference, no AI judgement. The skill question
 * is what makes a Greenside competition a competition of skill rather than a
 * lottery, so how it is judged has to be a rule an entrant could apply
 * themselves and get the same answer. "Close enough" is not such a rule.
 *
 * Normalisation, in this exact order:
 *   1. trim
 *   2. collapse internal whitespace
 *   3. casefold
 *   4. strip surrounding punctuation
 */

/** The rule version pinned onto every verdict. Bump ONLY with a migration plan. */
export const SKILL_RULE_VERSION = 'v1';

export type SkillVerdict = 'CORRECT' | 'INCORRECT' | 'UNJUDGED';

/** Punctuation stripped from the OUTSIDE of an answer only, never the inside. */
const SURROUNDING_PUNCTUATION = /^[\s!-/:-@[-`{-~]+|[\s!-/:-@[-`{-~]+$/g;

/**
 * Canonical comparison form.
 *
 * Applied identically to both sides, so `" Bunker! "` and `"bunker"` compare
 * equal while `"sand trap"` and `"bunker"` do not.
 */
export function normaliseAnswer(raw: string): string {
  return raw
    .trim()                      // 1. trim
    .replace(/\s+/g, ' ')        // 2. collapse internal whitespace
    .toLowerCase()               // 3. casefold
    .replace(SURROUNDING_PUNCTUATION, ''); // 4. strip surrounding punctuation
}

export interface SkillConfig {
  /** The options shown to the entrant. Empty means the question is free text. */
  answers: string[];
  /** The correct answer, from custom.skill_answer_correct. */
  correct: string | null;
}

export interface SkillConfigProblem {
  code: 'MISSING_CORRECT_ANSWER' | 'CORRECT_NOT_AN_OPTION' | 'DUPLICATE_OPTIONS';
  message: string;
}

/**
 * Validate a competition's skill configuration.
 *
 * A competition that cannot judge its own question must not be allowed to
 * open, because it can never be frozen for a draw.
 */
export function validateSkillConfig(config: SkillConfig): SkillConfigProblem[] {
  const problems: SkillConfigProblem[] = [];

  const correct = config.correct?.trim() ?? '';
  if (correct === '') {
    problems.push({
      code: 'MISSING_CORRECT_ANSWER',
      message: 'custom.skill_answer_correct is empty; the competition cannot judge its own question',
    });
  }

  const normalisedOptions = config.answers.map(normaliseAnswer).filter((a) => a !== '');

  // Duplicate options after normalisation make the question ambiguous: two
  // choices that judge identically cannot both be wrong or both be right.
  const seen = new Set<string>();
  const duplicates = new Set<string>();
  for (const option of normalisedOptions) {
    if (seen.has(option)) duplicates.add(option);
    seen.add(option);
  }
  if (duplicates.size > 0) {
    problems.push({
      code: 'DUPLICATE_OPTIONS',
      message: `skill_answers contains options that normalise identically: ${[...duplicates].join(', ')}`,
    });
  }

  // Multiple-choice mode: the correct answer must actually be on offer.
  // Free-text mode (no options) is valid and skips this check.
  if (correct !== '' && normalisedOptions.length > 0) {
    if (!seen.has(normaliseAnswer(correct))) {
      problems.push({
        code: 'CORRECT_NOT_AN_OPTION',
        message: `custom.skill_answer_correct ${JSON.stringify(correct)} is not one of the options in skill_answers`,
      });
    }
  }

  return problems;
}

/** True when the question is free text, i.e. no options were configured. */
export function isFreeText(config: SkillConfig): boolean {
  return config.answers.map(normaliseAnswer).filter((a) => a !== '').length === 0;
}

/**
 * Judge one entrant answer against the correct answer AS SNAPSHOTTED.
 *
 * `correctSnapshot` must come from the allocation row, never from a fresh read
 * of the product metafield. The metafield is editable; the snapshot is not.
 */
export function judge(entrantAnswer: string | null, correctSnapshot: string | null): SkillVerdict {
  const correct = correctSnapshot?.trim() ?? '';
  if (correct === '') return 'UNJUDGED';

  const given = entrantAnswer?.trim() ?? '';
  if (given === '') return 'INCORRECT';

  return normaliseAnswer(given) === normaliseAnswer(correct) ? 'CORRECT' : 'INCORRECT';
}

export interface SkillSnapshot {
  question: string | null;
  answer: string | null;
  correctSnapshot: string | null;
  verdict: SkillVerdict;
  ruleVersion: string | null;
  judgedAt: string | null;
}

/**
 * Build the immutable skill record written onto an allocation.
 *
 * Captures the question asked, the answer given, the correct answer as it
 * stood at that moment, the verdict, and the rule that produced it.
 */
export function snapshotSkill(args: {
  question: string | null;
  answer: string | null;
  correct: string | null;
  now: string;
}): SkillSnapshot {
  const verdict = judge(args.answer, args.correct);
  const judged = verdict !== 'UNJUDGED';
  return {
    question: args.question,
    answer: args.answer,
    correctSnapshot: args.correct,
    verdict,
    // The CHECK in migration 0003 requires both of these whenever a verdict
    // was actually reached.
    ruleVersion: judged ? SKILL_RULE_VERSION : null,
    judgedAt: judged ? args.now : null,
  };
}
