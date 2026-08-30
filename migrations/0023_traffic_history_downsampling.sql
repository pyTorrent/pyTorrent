-- pytorrent:applied-if SELECT COUNT(*) = 3 AS applied FROM pragma_table_info('traffic_history') WHERE name IN ('downloaded', 'uploaded', 'sample_count');
-- Note: Persisted transfer deltas and sample weights let housekeeping replace old minute rows without losing chart totals or weighted speed averages.
ALTER TABLE traffic_history ADD COLUMN downloaded INTEGER;
ALTER TABLE traffic_history ADD COLUMN uploaded INTEGER;
ALTER TABLE traffic_history ADD COLUMN sample_count INTEGER NOT NULL DEFAULT 1;
UPDATE traffic_history SET sample_count=1 WHERE sample_count IS NULL OR sample_count < 1;
