-- pytorrent:applied-if SELECT EXISTS(SELECT 1 FROM pragma_table_info('profile_preferences') WHERE name='footer_separators_enabled') AS applied;
-- Note: Footer item separators are a personal per-profile presentation preference and remain enabled unless the user turns them off.
ALTER TABLE profile_preferences ADD COLUMN footer_separators_enabled INTEGER DEFAULT 0;
UPDATE profile_preferences SET footer_separators_enabled=1 WHERE footer_separators_enabled IS NULL;
