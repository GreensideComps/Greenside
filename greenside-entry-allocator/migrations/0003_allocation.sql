-- One row per (order, line item). This is the idempotency boundary: processing
-- the same Shopify event twice must converge on this row, never create a second.
--
-- Every *_snapshot column exists because the Shopify value behind it is
-- editable after the fact. A merchant fixing a typo in the correct answer six
-- weeks into a competition must not silently re-judge entries already sold.

CREATE TABLE allocation (
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
  skill_verdict                 TEXT NOT NULL,  -- CORRECT | INCORRECT | UNJUDGED
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
  CHECK (skill_verdict IN ('CORRECT', 'INCORRECT', 'UNJUDGED')),
  CHECK (entries_per_unit >= 1),
  CHECK (ordered_quantity >= 0),
  CHECK (target_count >= 0),
  CHECK (held_count >= 0),
  -- A judged verdict must carry the rule that produced it.
  CHECK (skill_verdict = 'UNJUDGED' OR (skill_rule_version IS NOT NULL AND skill_judged_at IS NOT NULL))
);

CREATE INDEX ix_allocation_competition ON allocation (competition_id, status);
CREATE INDEX ix_allocation_order       ON allocation (order_id);
CREATE INDEX ix_allocation_mirror      ON allocation (mirror_state);
