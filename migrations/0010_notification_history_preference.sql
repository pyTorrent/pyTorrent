-- Note: Toast notification history is opt-in; only the enable flag is stored in SQLite.
-- pytorrent:applied-if SELECT COUNT(*) = 1 AS applied FROM pragma_table_info('user_preferences') WHERE name = 'notification_history_enabled';
ALTER TABLE user_preferences ADD COLUMN notification_history_enabled INTEGER DEFAULT 0;
