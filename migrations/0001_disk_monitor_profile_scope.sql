-- Note: Disk monitoring became profile-scoped while preserving the most recently updated row for each profile.
-- pytorrent:applied-if SELECT COALESCE((SELECT group_concat(name, ',') FROM (SELECT name FROM pragma_table_info('disk_monitor_preferences') WHERE pk > 0 ORDER BY pk)), '') = 'profile_id' AS applied;
DROP INDEX IF EXISTS idx_disk_monitor_preferences_owner;
DROP TABLE IF EXISTS disk_monitor_preferences_new;
DROP TABLE IF EXISTS disk_monitor_preferences_old_user_profile;

CREATE TABLE disk_monitor_preferences_new (
  profile_id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  paths_json TEXT,
  mode TEXT DEFAULT 'default',
  selected_path TEXT,
  stop_enabled INTEGER DEFAULT 0,
  stop_threshold INTEGER DEFAULT 98,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id),
  FOREIGN KEY(profile_id) REFERENCES rtorrent_profiles(id)
);

INSERT INTO disk_monitor_preferences_new(
  profile_id, user_id, paths_json, mode, selected_path, stop_enabled, stop_threshold, created_at, updated_at
)
SELECT profile_id, user_id, paths_json, mode, selected_path, stop_enabled, stop_threshold,
       COALESCE(created_at, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),
       COALESCE(updated_at, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
FROM (
  SELECT d.*,
         ROW_NUMBER() OVER (
           PARTITION BY profile_id
           ORDER BY COALESCE(updated_at, created_at, '') DESC, user_id ASC
         ) AS rn
  FROM disk_monitor_preferences d
  WHERE profile_id IS NOT NULL
)
WHERE rn = 1;

ALTER TABLE disk_monitor_preferences RENAME TO disk_monitor_preferences_old_user_profile;
ALTER TABLE disk_monitor_preferences_new RENAME TO disk_monitor_preferences;
CREATE INDEX IF NOT EXISTS idx_disk_monitor_preferences_owner ON disk_monitor_preferences(user_id);
