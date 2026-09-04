-- Note: Additional profile storage roots are shared download locations used by the Storage browser and are independent from Disk Monitor paths.
-- pytorrent:applied-if SELECT EXISTS(SELECT 1 FROM pragma_table_info('disk_monitor_preferences') WHERE name='storage_roots_json') AS applied;
ALTER TABLE disk_monitor_preferences ADD COLUMN storage_roots_json TEXT DEFAULT '[]';
UPDATE disk_monitor_preferences SET storage_roots_json='[]' WHERE storage_roots_json IS NULL OR trim(storage_roots_json)='';
