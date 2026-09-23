-- Competition configuration and lifecycle.
--
-- Capacity is competition DATA. It is never inferred from an entry number, and
-- an entry number is never parsed to recover it.
--
-- A range is INCLUSIVE of both endpoints:
--   prefix PUT, start_number 1001, capacity 100  ->  PUT1001 .. PUT1100
--   that is exactly 100 numbers, because 1100 - 1001 + 1 = 100.

CREATE TABLE competition (
  competition_id            TEXT    PRIMARY KEY,   -- numeric Shopify product id, as text
  shop_domain               TEXT    NOT NULL,
  product_gid               TEXT    NOT NULL,      -- gid://shopify/Product/<id>

  prefix                    TEXT    NOT NULL,      -- canonical uppercase, e.g. 'PUT'
  start_number              INTEGER NOT NULL,      -- e.g. 1001
  capacity                  INTEGER NOT NULL,      -- e.g. 100  (custom.entries_total)
  pad_width                 INTEGER NOT NULL,      -- e.g. 4    -> PUT1001

  handle_snapshot           TEXT    NOT NULL,
  title_snapshot            TEXT    NOT NULL,

  -- Snapshotted so a later metafield edit cannot rewrite how past entries were
  -- judged. The per-allocation snapshot in `allocation` is the one that governs
  -- a verdict; this is the competition-level record of what was configured.
  skill_question_snapshot   TEXT,
  skill_answers_snapshot    TEXT,                  -- JSON array as configured
  skill_answer_correct_snapshot TEXT,

  status                    TEXT    NOT NULL,      -- DRAFT | OPEN | FROZEN
  pool_built_at             TEXT,
  frozen_at                 TEXT,

  created_at                TEXT    NOT NULL,
  updated_at                TEXT    NOT NULL,

  -- Stored canonically uppercase. Combined with the NOCASE unique index below
  -- this stops 'PUT' and 'put' ever coexisting.
  CHECK (prefix = UPPER(prefix)),
  CHECK (LENGTH(prefix) BETWEEN 2 AND 6),
  CHECK (prefix GLOB '[A-Z]*'),
  CHECK (prefix NOT GLOB '*[^A-Z]*'),

  CHECK (capacity > 0),
  CHECK (start_number >= 0),
  CHECK (pad_width BETWEEN 1 AND 9),

  -- The highest number in the range must fit the declared padding, otherwise
  -- PUT1 .. PUT100 sorts and reads as a mistake.
  CHECK (LENGTH(CAST(start_number + capacity - 1 AS TEXT)) <= pad_width),

  CHECK (status IN ('DRAFT', 'OPEN', 'FROZEN')),
  CHECK (status <> 'FROZEN' OR frozen_at IS NOT NULL)
);

-- Layer 1 of prefix uniqueness: one competition per prefix, case-insensitively.
CREATE UNIQUE INDEX ux_competition_prefix ON competition (prefix COLLATE NOCASE);

CREATE UNIQUE INDEX ux_competition_product ON competition (shop_domain, product_gid);
