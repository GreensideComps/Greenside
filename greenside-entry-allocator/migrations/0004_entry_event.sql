-- Append-only history. Nothing in this table is ever updated or deleted.
--
-- `entry_number` holds CURRENT state; this holds EVERYTHING THAT HAPPENED. That
-- split is what lets a number be reissued without losing who held it before:
--
--   PUT1001  ALLOCATED         alloc#1  order #1042  2026-10-01T14:02Z
--   PUT1001  RELEASED          alloc#1  order #1042  refund  2026-10-03T09:11Z
--   PUT1001  RETURNED_TO_POOL  -        -                    2026-10-03T09:11Z
--   PUT1001  ALLOCATED         alloc#2  order #1057  2026-10-04T18:40Z

CREATE TABLE entry_event (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at     TEXT    NOT NULL,
  competition_id  TEXT    NOT NULL,

  seq             INTEGER,               -- set when the event concerns one number
  entry_number    TEXT,
  allocation_id   TEXT,
  allocation_seq  INTEGER,

  event_type      TEXT    NOT NULL,
  from_status     TEXT,
  to_status       TEXT,

  order_id        TEXT,
  customer_ref    TEXT,
  reason          TEXT,

  actor           TEXT    NOT NULL,      -- system:webhook | system:reconcile | system:cli | staff:<id>
  run_id          TEXT    NOT NULL,
  webhook_id      TEXT,                  -- X-Shopify-Webhook-Id, recorded not trusted
  detail_json     TEXT    NOT NULL,

  CHECK (event_type IN (
    'POOL_BUILT', 'POOL_GROWN', 'POOL_SHRINK_REFUSED',
    'ALLOCATED', 'RELEASED', 'RETURNED_TO_POOL',
    'CONFIG_INVALID', 'REFUSED_PREFIX_TAKEN', 'REFUSED_CAPACITY',
    'INCORRECT_SKILL', 'UNJUDGED_SKILL',
    'FROZEN', 'FREEZE_REFUSED',
    'WEBHOOK_RECEIVED', 'WEBHOOK_REJECTED', 'WEBHOOK_SKIPPED',
    'ORDER_HELD', 'ORDER_INELIGIBLE',
    'RECONCILED', 'REPAIRED', 'INVARIANT_VIOLATION',
    'MIRRORED', 'MIRROR_FAILED',
    'ERROR'
  ))
);

CREATE INDEX ix_event_number     ON entry_event (competition_id, seq, id);
CREATE INDEX ix_event_order      ON entry_event (order_id);
CREATE INDEX ix_event_allocation ON entry_event (allocation_id);
CREATE INDEX ix_event_type       ON entry_event (event_type, occurred_at);

-- Observability into Shopify's retry behaviour. NOT the idempotency mechanism:
-- the reconciler has no webhook id at all.
CREATE TABLE webhook_delivery (
  webhook_id  TEXT PRIMARY KEY,
  topic       TEXT NOT NULL,
  received_at TEXT NOT NULL,
  outcome     TEXT NOT NULL
);
