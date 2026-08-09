-- Note: Store the active speed limit independently for each rTorrent profile.
-- pytorrent:applied-if SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'profile_speed_limits') AS applied;
CREATE TABLE IF NOT EXISTS profile_speed_limits (
  profile_id INTEGER PRIMARY KEY,
  down_limit INTEGER DEFAULT 0,
  up_limit INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(profile_id) REFERENCES rtorrent_profiles(id) ON DELETE CASCADE
);
