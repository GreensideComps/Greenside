-- Skill verdict NOT_REQUIRED, for competitions that ask no skill question
-- (product metafield custom.skill_mode = none).
--
-- NOT_REQUIRED is not a judgement. It records that there was nothing to judge,
-- so, like UNJUDGED, it carries no rule version and no judged_at. Unlike
-- UNJUDGED it is eligible for the draw (see SELECT_FROZEN_SNAPSHOT).
--
-- SQLite cannot alter a CHECK constraint, so the table is rebuilt: create the
-- new shape, copy every row unchanged, swap, and recreate the indexes. The
-- columns, their order, UNIQUE (order_id, line_item_id) and every other CHECK
-- are exactly those of 0003_allocation.sql; only the two skill_verdict CHECKs
-- change. Nothing references this table by foreign key, and it has no
-- triggers.

CREATE TABLE allocation_new (
  allocation_id     TEXT    PRIMARY KEY,   -- sha256('gsalloc:v1:'||shop||':'||order_id||':'||line_item_id)
  shop_domain       TEXT    NOT NULL,
  competition_id    TEXT    NOT NULL,

  order_id          TEXT    NOT NULL,
  order_name        TEXT    NOT NULL,      -- '#1042'
  order_created_at  TEXT    NOT NULL,
  line_item_id      TEXT    NOT NULL,
  variant_id        TEXT,

  -- Identity only. No email, no name, no address. NULL for a guest checkout,
  -- which shop.customerAccounts = OPTIONAL permits.
  customer_ref      TEXT,

  entry_route       TEXT    NOT NULL,      -- online | postal | unknown

  ordered_quantity  INTEGER NOT NULL,      -- lineItem.quantity, the immutable original
  entries_per_unit  INTEGER NOT NULL,      -- snapshot of variant custom.entries (default 1, floor 1)
  target_count      INTEGER NOT NULL,      -- currentQuantity x entries_per_unit at last evaluation
  held_count        INTEGER NOT NULL,      -- numbers currently ALLOCATED to this allocation

  skill_question                TEXT,      -- snapshot of the _skill_question property
  skill_answer                  TEXT,      -- snapshot of the 'Skill answer' property
  skill_answer_correct_snapshot TEXT,      -- snapshot of custom.skill_answer_correct AS AT ALLOCATION
  skill_verdict                 TEXT NOT NULL,  -- CORRECT | INCORRECT | UNJUDGED | NOT_REQUIRED
  skill_judged_at               TEXT,
  skill_rule_version            TEXT,      -- e.g. 'v1' — so a verdict can be re-derived and defended

  unit_price_minor  INTEGER NOT NULL,      -- discountedUnitPriceAfterAllDiscountsSet, in pence
  line_total_minor  INTEGER NOT NULL,      -- discountedTotalSet(withCodeDiscounts: true), in pence

  -- What was OBSERVED at decision time, never recomputed. Answers
  -- "on what basis was this entry issued?"
  decision_basis    TEXT    NOT NULL,      -- json {financial_status, cancelled_at, test}

  status            TEXT    NOT NULL,      -- ALLOCATED | PARTIAL | RELEASED
  source            TEXT    NOT NULL,      -- webhook:orders/paid | reconcile | manual
  mirror_state      TEXT    NOT NULL,      -- PENDING | SYNCED | FAILED
  mirror_attempts   INTEGER NOT NULL DEFAULT 0,

  created_at        TEXT    NOT NULL,
  updated_at        TEXT    NOT NULL,

  -- Second, independent guard against a hashing defect.
  UNIQUE (order_id, line_item_id),

  CHECK (status IN ('ALLOCATED', 'PARTIAL', 'RELEASED')),
  CHECK (mirror_state IN ('PENDING', 'SYNCED', 'FAILED')),
  CHECK (skill_verdict IN ('CORRECT', 'INCORRECT', 'UNJUDGED', 'NOT_REQUIRED')),
  CHECK (entries_per_unit >= 1),
  CHECK (ordered_quantity >= 0),
  CHECK (target_count >= 0),
  CHECK (held_count >= 0),
  -- A judged verdict must carry the rule that produced it. UNJUDGED and
  -- NOT_REQUIRED are not judgements.
  CHECK (skill_verdict IN ('UNJUDGED', 'NOT_REQUIRED') OR (skill_rule_version IS NOT NULL AND skill_judged_at IS NOT NULL))
);

INSERT INTO allocation_new (
  allocation_id, shop_domain, competition_id, order_id, order_name, order_created_at,
  line_item_id, variant_id, customer_ref, entry_route, ordered_quantity, entries_per_unit,
  target_count, held_count, skill_question, skill_answer, skill_answer_correct_snapshot,
  skill_verdict, skill_judged_at, skill_rule_version, unit_price_minor, line_total_minor,
  decision_basis, status, source, mirror_state, mirror_attempts, created_at, updated_at
)
SELECT
  allocation_id, shop_domain, competition_id, order_id, order_name, order_created_at,
  line_item_id, variant_id, customer_ref, entry_route, ordered_quantity, entries_per_unit,
  target_count, held_count, skill_question, skill_answer, skill_answer_correct_snapshot,
  skill_verdict, skill_judged_at, skill_rule_version, unit_price_minor, line_total_minor,
  decision_basis, status, source, mirror_state, mirror_attempts, created_at, updated_at
FROM allocation;

DROP TABLE allocation;

ALTER TABLE allocation_new RENAME TO allocation;

CREATE INDEX ix_allocation_competition ON allocation (competition_id, status);
CREATE INDEX ix_allocation_order       ON allocation (order_id);
CREATE INDEX ix_allocation_mirror      ON allocation (mirror_state);
