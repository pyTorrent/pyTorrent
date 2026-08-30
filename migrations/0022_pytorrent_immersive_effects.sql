-- pytorrent:applied-if SELECT COUNT(*) = 1 AS applied FROM pragma_table_info('user_preferences') WHERE name = 'pytorrent_immersive_effects_enabled';
-- Note: Immersive PyTorrent theme effects are an optional second visual level and default off for existing and new users.
ALTER TABLE user_preferences ADD COLUMN pytorrent_immersive_effects_enabled INTEGER DEFAULT 0;
UPDATE user_preferences SET pytorrent_immersive_effects_enabled=0 WHERE pytorrent_immersive_effects_enabled IS NULL;
