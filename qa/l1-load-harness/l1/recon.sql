-- L1 independent D1 reconciliation (read-only). One SELECT per check; each returns one row with column `bad` (0 = PASS).
-- Parameters: :cid L1 competition id, :start 1001, :last 2650 (start + expected units - 1), :cap 2000, :units 1650, :orders 750.
-- Run offline against SQLite (tests) or one check at a time through `gs d1 query` (loadrecon.py renders literals).
-- check: competition_open
SELECT ((SELECT status FROM competition WHERE competition_id = :cid) IS NOT 'OPEN') + ((SELECT capacity FROM competition WHERE competition_id = :cid) IS NOT :cap) AS bad;
-- check: pool_counts
SELECT ((SELECT COUNT(*) FROM entry_number WHERE competition_id = :cid AND status = 'ALLOCATED') <> :units) + ((SELECT COUNT(*) FROM entry_number WHERE competition_id = :cid AND status = 'AVAILABLE') <> :cap - :units) + ((SELECT COUNT(*) FROM entry_number WHERE competition_id = :cid AND status NOT IN ('ALLOCATED', 'AVAILABLE')) <> 0) + ((SELECT COUNT(*) FROM entry_number WHERE competition_id = :cid) <> :cap) AS bad;
-- check: allocated_range
SELECT COUNT(*) AS bad FROM entry_number WHERE competition_id = :cid AND ((status = 'ALLOCATED' AND (seq < :start OR seq > :last)) OR (status = 'AVAILABLE' AND seq <= :last));
-- check: issued_once
SELECT COUNT(*) AS bad FROM entry_number WHERE competition_id = :cid AND ((status = 'ALLOCATED' AND allocation_seq <> 1) OR (status = 'AVAILABLE' AND allocation_seq <> 0));
-- check: duplicate_numbers
SELECT COUNT(*) AS bad FROM (SELECT entry_number FROM entry_number GROUP BY entry_number HAVING COUNT(*) > 1);
-- check: allocation_count
SELECT ((SELECT COUNT(*) FROM allocation WHERE competition_id = :cid) <> :orders) AS bad;
-- check: units_sum
SELECT (COALESCE((SELECT SUM(ordered_quantity) FROM allocation WHERE competition_id = :cid), 0) <> :units) AS bad;
-- check: ledger_counts
SELECT COUNT(*) AS bad FROM allocation a WHERE a.competition_id = :cid AND (a.target_count <> a.ordered_quantity * a.entries_per_unit OR a.held_count <> a.target_count OR a.status <> 'ALLOCATED' OR a.held_count <> (SELECT COUNT(*) FROM entry_number n WHERE n.allocation_id = a.allocation_id AND n.status = 'ALLOCATED'));
-- check: contiguous_blocks
SELECT COUNT(*) AS bad FROM (SELECT allocation_id, MAX(seq) - MIN(seq) + 1 AS span, COUNT(*) AS c FROM entry_number WHERE competition_id = :cid AND status = 'ALLOCATED' GROUP BY allocation_id) WHERE span <> c;
-- check: entry_matches_allocation
SELECT COUNT(*) AS bad FROM entry_number n LEFT JOIN allocation a ON a.allocation_id = n.allocation_id WHERE n.competition_id = :cid AND n.status = 'ALLOCATED' AND (a.allocation_id IS NULL OR n.order_id <> a.order_id OR n.line_item_id <> a.line_item_id OR a.competition_id <> :cid);
-- check: one_allocation_per_line
SELECT COUNT(*) AS bad FROM (SELECT order_id, line_item_id FROM allocation WHERE competition_id = :cid GROUP BY 1, 2 HAVING COUNT(*) > 1);
-- check: zero_value
SELECT COUNT(*) AS bad FROM allocation WHERE competition_id = :cid AND (unit_price_minor <> 0 OR line_total_minor <> 0);
-- check: event_types
SELECT COUNT(*) AS bad FROM entry_event WHERE competition_id = :cid AND event_type <> 'ALLOCATED';
-- check: event_count
SELECT ((SELECT COUNT(*) FROM entry_event WHERE competition_id = :cid AND event_type = 'ALLOCATED') <> :units) AS bad;
-- check: event_matches_pool
SELECT COUNT(*) AS bad FROM entry_number n WHERE n.competition_id = :cid AND n.status = 'ALLOCATED' AND (SELECT COUNT(*) FROM entry_event e WHERE e.competition_id = n.competition_id AND e.seq = n.seq AND e.allocation_seq = n.allocation_seq AND e.event_type = 'ALLOCATED' AND e.allocation_id = n.allocation_id AND e.entry_number = n.entry_number AND e.order_id = n.order_id) <> 1;
-- check: duplicate_issue
SELECT COUNT(*) AS bad FROM (SELECT seq, allocation_seq FROM entry_event WHERE competition_id = :cid AND event_type = 'ALLOCATED' GROUP BY 1, 2 HAVING COUNT(*) > 1);
-- check: ledger_drift_global
SELECT COUNT(*) AS bad FROM allocation a WHERE a.held_count <> (SELECT COUNT(*) FROM entry_number n WHERE n.allocation_id = a.allocation_id AND n.status = 'ALLOCATED');
-- check: orphans_global
SELECT COUNT(*) AS bad FROM entry_number n WHERE n.allocation_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM allocation a WHERE a.allocation_id = n.allocation_id);
-- check: audit_gaps_global
SELECT COUNT(*) AS bad FROM entry_number n WHERE n.status = 'ALLOCATED' AND NOT EXISTS (SELECT 1 FROM entry_event e WHERE e.entry_number = n.entry_number AND e.competition_id = n.competition_id);
