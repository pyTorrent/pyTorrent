-- Note: Job and operation logs use independent retention settings while preserving legacy operation retention values.
-- pytorrent:applied-if SELECT COUNT(*) = 13 AS applied FROM pragma_table_info('operation_log_settings') WHERE name IN ('retention_interval_hours', 'job_retention_mode', 'job_retention_days', 'job_retention_lines', 'job_retention_interval_hours', 'job_last_retention_run_at', 'job_last_retention_deleted', 'operation_retention_mode', 'operation_retention_days', 'operation_retention_lines', 'operation_retention_interval_hours', 'operation_last_retention_run_at', 'operation_last_retention_deleted');
ALTER TABLE operation_log_settings ADD COLUMN retention_interval_hours INTEGER DEFAULT 24;
ALTER TABLE operation_log_settings ADD COLUMN job_retention_mode TEXT DEFAULT 'days';
ALTER TABLE operation_log_settings ADD COLUMN job_retention_days INTEGER DEFAULT 7;
ALTER TABLE operation_log_settings ADD COLUMN job_retention_lines INTEGER DEFAULT 2000;
ALTER TABLE operation_log_settings ADD COLUMN job_retention_interval_hours INTEGER DEFAULT 24;
ALTER TABLE operation_log_settings ADD COLUMN job_last_retention_run_at TEXT;
ALTER TABLE operation_log_settings ADD COLUMN job_last_retention_deleted INTEGER DEFAULT 0;
ALTER TABLE operation_log_settings ADD COLUMN operation_retention_mode TEXT DEFAULT 'days';
ALTER TABLE operation_log_settings ADD COLUMN operation_retention_days INTEGER DEFAULT 30;
ALTER TABLE operation_log_settings ADD COLUMN operation_retention_lines INTEGER DEFAULT 5000;
ALTER TABLE operation_log_settings ADD COLUMN operation_retention_interval_hours INTEGER DEFAULT 24;
ALTER TABLE operation_log_settings ADD COLUMN operation_last_retention_run_at TEXT;
ALTER TABLE operation_log_settings ADD COLUMN operation_last_retention_deleted INTEGER DEFAULT 0;

UPDATE operation_log_settings
SET operation_retention_mode=COALESCE(operation_retention_mode, retention_mode, 'days'),
    operation_retention_days=COALESCE(operation_retention_days, retention_days, 30),
    operation_retention_lines=COALESCE(operation_retention_lines, retention_lines, 5000),
    operation_retention_interval_hours=COALESCE(operation_retention_interval_hours, retention_interval_hours, 24),
    job_retention_mode=COALESCE(job_retention_mode, 'days'),
    job_retention_days=COALESCE(job_retention_days, 7),
    job_retention_lines=COALESCE(job_retention_lines, 2000),
    job_retention_interval_hours=COALESCE(job_retention_interval_hours, retention_interval_hours, 24),
    updated_at=COALESCE(updated_at, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'));
