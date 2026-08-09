-- Note: Persist sidebar expansion state per user/profile.
-- pytorrent:applied-if SELECT COUNT(*) = 2 AS applied FROM pragma_table_info('profile_preferences') WHERE name IN ('sidebar_labels_expanded', 'sidebar_shortcuts_expanded');
ALTER TABLE profile_preferences ADD COLUMN sidebar_labels_expanded INTEGER DEFAULT 0;
ALTER TABLE profile_preferences ADD COLUMN sidebar_shortcuts_expanded INTEGER DEFAULT 0;
