-- pytorrent:applied-if SELECT COUNT(*) = 1 AS applied FROM pragma_table_info('user_preferences') WHERE name = 'theme_status_accents_enabled';
-- Note: Theme-aware progress and success badges are opt-in so existing installations keep the shared legacy palette.
ALTER TABLE user_preferences ADD COLUMN theme_status_accents_enabled INTEGER DEFAULT 0;
UPDATE user_preferences SET theme_status_accents_enabled=0 WHERE theme_status_accents_enabled IS NULL;
