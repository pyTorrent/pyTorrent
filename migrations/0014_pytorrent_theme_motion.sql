-- pytorrent:applied-if SELECT COUNT(*) = 2 AS applied FROM pragma_table_info('user_preferences') WHERE name IN ('pytorrent_theme', 'pytorrent_animations_enabled');
-- Note: PyTorrent owns its theme and motion preferences independently from Bootstrap.
ALTER TABLE user_preferences ADD COLUMN pytorrent_theme TEXT DEFAULT 'default-beta';
ALTER TABLE user_preferences ADD COLUMN pytorrent_animations_enabled INTEGER DEFAULT 1;
UPDATE user_preferences
SET pytorrent_theme = CASE
  WHEN bootstrap_theme LIKE 'pytorrent-%' THEN bootstrap_theme
  ELSE 'default-beta'
END
WHERE pytorrent_theme IS NULL OR trim(pytorrent_theme) = '' OR pytorrent_theme = 'default-beta';
UPDATE user_preferences SET pytorrent_animations_enabled=1 WHERE pytorrent_animations_enabled IS NULL;
