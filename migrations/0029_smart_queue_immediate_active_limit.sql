-- Note: Smart Queue can optionally enforce active overflow immediately instead of sharing the stalled stop batch limit.
-- pytorrent:applied-if SELECT EXISTS(SELECT 1 FROM pragma_table_info('smart_queue_settings') WHERE name='enforce_active_limit_immediately') AS applied;
ALTER TABLE smart_queue_settings ADD COLUMN enforce_active_limit_immediately INTEGER DEFAULT 1;
UPDATE smart_queue_settings SET enforce_active_limit_immediately=1 WHERE enforce_active_limit_immediately IS NULL;
