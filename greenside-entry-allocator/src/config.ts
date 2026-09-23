/**
 * Competition configuration: Shopify product metafields -> a validated shape.
 *
 * Pure apart from being handed raw metafield values. The default answer is
 * REFUSE: a competition whose configuration is incomplete or contradictory
 * never gets a pool, because a pool that cannot be judged can never be frozen
 * for a draw.
 *
 * Capacity comes from custom.entries_total -- competition DATA. It is never
 * inferred from an entry number, and an entry number is never parsed to
 * recover it.
 */

import {
  canonicalPrefix,
  defaultPadWidth,
  validateNumbering,
  type NumberingConfig,
} from './numbering';
import { validateSkillConfig, type SkillConfig } from './skill';

export interface RawCompetitionMetafields {
  productGid: string;
  handle: string;
  title: string;
  productStatus: string;
  entriesTotal: string | null;
  entryPrefix: string | null;
  entryStartNumber: string | null;
  skillQuestion: string | null;
  /** JSON array as stored by a list.single_line_text_field metafield. */
  skillAnswers: string | null;
  skillAnswerCorrect: string | null;
}

export interface CompetitionConfig {
  competitionId: string;
  productGid: string;
  handle: string;
  title: string;
  numbering: NumberingConfig;
  skill: SkillConfig;
  skillQuestion: string | null;
}

export interface ConfigProblem {
  code: string;
  message: string;
}

export type ConfigResult =
  | { ok: true; config: CompetitionConfig }
  | { ok: false; problems: ConfigProblem[] };

/** Numeric id out of a gid, e.g. gid://shopify/Product/123 -> '123'. */
export function numericId(gid: string): string {
  const tail = gid.split('/').pop();
  return tail && tail !== '' ? tail : gid;
}

/** list.single_line_text_field values arrive as a JSON array string. */
export function parseAnswerList(raw: string | null): string[] {
  if (raw === null || raw.trim() === '') return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((v): v is string => typeof v === 'string');
  } catch {
    return [];
  }
}

/** Strict integer parse. Rejects '12abc', '1.5', '' and whitespace. */
function parseIntStrict(raw: string | null): number | null {
  if (raw === null) return null;
  const trimmed = raw.trim();
  if (!/^-?\d+$/.test(trimmed)) return null;
  return Number.parseInt(trimmed, 10);
}

export function buildCompetitionConfig(raw: RawCompetitionMetafields): ConfigResult {
  const problems: ConfigProblem[] = [];

  const prefixRaw = raw.entryPrefix?.trim() ?? '';
  if (prefixRaw === '') {
    problems.push({ code: 'MISSING_PREFIX', message: 'custom.entry_prefix is not set' });
  }
  const prefix = canonicalPrefix(prefixRaw);

  const startNumber = parseIntStrict(raw.entryStartNumber);
  if (startNumber === null) {
    problems.push({
      code: 'MISSING_START_NUMBER',
      message: `custom.entry_start_number is missing or not an integer (got ${JSON.stringify(raw.entryStartNumber)})`,
    });
  }

  const capacity = parseIntStrict(raw.entriesTotal);
  if (capacity === null) {
    problems.push({
      code: 'MISSING_CAPACITY',
      message: `custom.entries_total is missing or not an integer (got ${JSON.stringify(raw.entriesTotal)})`,
    });
  }

  // Only worth validating the range once the parts parsed.
  let numbering: NumberingConfig | null = null;
  if (startNumber !== null && capacity !== null && prefixRaw !== '') {
    numbering = {
      prefix,
      startNumber,
      capacity,
      padWidth: defaultPadWidth(startNumber, capacity),
    };
    for (const message of validateNumbering(numbering)) {
      problems.push({ code: 'INVALID_NUMBERING', message });
    }
  }

  const skill: SkillConfig = {
    answers: parseAnswerList(raw.skillAnswers),
    correct: raw.skillAnswerCorrect,
  };
  for (const problem of validateSkillConfig(skill)) {
    problems.push({ code: problem.code, message: problem.message });
  }

  if (problems.length > 0 || numbering === null) {
    return { ok: false, problems };
  }

  return {
    ok: true,
    config: {
      competitionId: numericId(raw.productGid),
      productGid: raw.productGid,
      handle: raw.handle,
      title: raw.title,
      numbering,
      skill,
      skillQuestion: raw.skillQuestion,
    },
  };
}

/**
 * Entries contained in one unit of a variant.
 *
 * Mirrors snippets/entry-selector.liquid exactly, including the default of 1
 * and the floor at 1. If the allocator and the storefront disagreed, an
 * entrant would be shown one number of entries and issued another.
 */
export function entriesPerUnit(rawVariantEntries: string | null): number {
  const parsed = parseIntStrict(rawVariantEntries);
  if (parsed === null || parsed < 1) return 1;
  return parsed;
}
