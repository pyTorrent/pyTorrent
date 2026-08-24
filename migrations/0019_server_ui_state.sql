-- Note: Small UI state previously kept in browser-local storage is persisted by pyTorrent so SQLite/RAM remains the application source of truth.
-- pytorrent:applied-if SELECT (EXISTS(SELECT 1 FROM pragma_table_info('user_preferences') WHERE name='user_ui_state_json') AND EXISTS(SELECT 1 FROM pragma_table_info('profile_preferences') WHERE name='profile_ui_state_json')) AS applied;
ALTER TABLE user_preferences ADD COLUMN user_ui_state_json TEXT DEFAULT '{}';
ALTER TABLE profile_preferences ADD COLUMN profile_ui_state_json TEXT DEFAULT '{}';
UPDATE user_preferences SET user_ui_state_json='{}' WHERE user_ui_state_json IS NULL;
UPDATE profile_preferences SET profile_ui_state_json='{}' WHERE profile_ui_state_json IS NULL;
