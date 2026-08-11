-- Note: Notification history can keep only important Toasts or every Toast without changing Toast visibility.
-- pytorrent:applied-if SELECT COUNT(*) = 1 AS applied FROM pragma_table_info('user_preferences') WHERE name = 'notification_history_mode';
ALTER TABLE user_preferences ADD COLUMN notification_history_mode TEXT DEFAULT 'important';
-- Note: Existing users who already enabled history keep the previous all-Toasts behavior until they choose the quieter mode.
UPDATE user_preferences SET notification_history_mode='all' WHERE notification_history_enabled=1;
