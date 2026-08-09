-- Note: Store CPU/RAM chart layout and expanded state per user and rTorrent profile.
-- pytorrent:applied-if SELECT COUNT(*) = 2 AS applied FROM pragma_table_info('profile_preferences') WHERE name IN ('system_usage_chart_mode', 'system_usage_chart_expanded');
ALTER TABLE profile_preferences ADD COLUMN system_usage_chart_mode TEXT DEFAULT 'combined';
ALTER TABLE profile_preferences ADD COLUMN system_usage_chart_expanded INTEGER DEFAULT 0;
