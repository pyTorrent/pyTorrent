-- pytorrent:applied-if SELECT COUNT(*) = 1 AS applied FROM pragma_table_info('user_preferences') WHERE name = 'toast_notification_mode';
-- Note: Visible Toast filtering is independent from browser-local notification history filtering.
ALTER TABLE user_preferences ADD COLUMN toast_notification_mode TEXT DEFAULT 'all';
