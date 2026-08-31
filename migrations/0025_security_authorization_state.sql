-- pytorrent:applied-if SELECT (EXISTS(SELECT 1 FROM pragma_table_info('operation_logs') WHERE name='actor_type') AND EXISTS(SELECT 1 FROM pragma_table_info('automation_history') WHERE name='actor_type') AND EXISTS(SELECT 1 FROM pragma_table_info('ratio_history') WHERE name='actor_type') AND EXISTS(SELECT 1 FROM pragma_table_info('rss_history') WHERE name='job_id') AND EXISTS(SELECT 1 FROM pragma_table_info('ratio_assignments') WHERE name='pending_job_id')) AS applied;
-- Disable legacy profile-transfer automation rules once so re-enabling them must pass current backend target authorization.
UPDATE automation_rules
SET enabled=0,
    updated_at=CURRENT_TIMESTAMP
WHERE enabled=1
  AND effects_json LIKE '%profile_transfer%';

ALTER TABLE operation_logs ADD COLUMN actor_type TEXT NOT NULL DEFAULT 'user';
ALTER TABLE automation_history ADD COLUMN actor_type TEXT NOT NULL DEFAULT 'user';
ALTER TABLE ratio_history ADD COLUMN actor_type TEXT NOT NULL DEFAULT 'user';
ALTER TABLE rss_history ADD COLUMN job_id TEXT;
ALTER TABLE ratio_assignments ADD COLUMN pending_job_id TEXT;
