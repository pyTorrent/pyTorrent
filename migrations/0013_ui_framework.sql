-- pytorrent:applied-if SELECT COUNT(*) = 1 AS applied FROM pragma_table_info('user_preferences') WHERE name = 'ui_framework';
-- Note: Bootstrap stays the default while the standalone PyTorrent CSS framework is available as an opt-in beta.
ALTER TABLE user_preferences ADD COLUMN ui_framework TEXT DEFAULT 'bootstrap';
UPDATE user_preferences SET ui_framework='bootstrap' WHERE ui_framework IS NULL OR trim(ui_framework)='';
