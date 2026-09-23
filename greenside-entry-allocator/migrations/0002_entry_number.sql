-- The entry-number pool. One row per number, materialised when the pool is
-- built. This is an explicit pool, NOT a counter: "next number" is always the
-- lowest row whose status is AVAILABLE, so a released number is reusable
-- without any arithmetic about how many orders have been placed.

CREATE TABLE entry_number (
  competition_id  TEXT    NOT NULL,
  seq             INTEGER NOT NULL,   -- the numeric sequence, e.g. 1001
  entry_number    TEXT    NOT NULL,   -- the rendered string, e.g. 'PUT1001'

  status          TEXT    NOT NULL,   -- AVAILABLE | ALLOCATED | RELEASED
  allocation_id   TEXT,               -- stamped by the claiming UPDATE itself
  order_id        TEXT,
  line_item_id    TEXT,
  customer_ref    TEXT,               -- numeric Shopify customer id, or NULL for a guest

  -- How many times this number has been issued. PUT1001 issue #1 and issue #2
  -- are different allocations of the same number and must never be confused.
  allocation_seq  INTEGER NOT NULL DEFAULT 0,

  allocated_at    TEXT,
  released_at     TEXT,
  release_reason  TEXT,               -- REFUND | CANCELLED | ORDER_EDIT | CHARGEBACK | MANUAL | REPAIR

  PRIMARY KEY (competition_id, seq),

  CHECK (status IN ('AVAILABLE', 'ALLOCATED', 'RELEASED')),
  CHECK (allocation_seq >= 0),
  -- An ALLOCATED row must name its allocation; a free row must not.
  CHECK ((status = 'ALLOCATED') = (allocation_id IS NOT NULL)),
  CHECK (status <> 'ALLOCATED' OR allocated_at IS NOT NULL)
);

-- Layer 2 of prefix uniqueness, and the constraint that actually protects a
-- customer: the RENDERED string is globally unique across every competition.
-- Even if two competitions somehow shared a prefix and overlapped, the second
-- pool build would fail here rather than issue a duplicate 'PUT1001'.
CREATE UNIQUE INDEX ux_entry_number_global ON entry_number (entry_number);

-- Supports the lowest-available-first claim.
CREATE INDEX ix_entry_pool_pick  ON entry_number (competition_id, status, seq);
CREATE INDEX ix_entry_allocation ON entry_number (allocation_id);
CREATE INDEX ix_entry_order      ON entry_number (order_id);
