-- Note: Persist lightweight per-profile runtime totals used by status and dashboard views.
-- pytorrent:applied-if SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'profile_runtime_stats') AS applied;
CREATE TABLE IF NOT EXISTS profile_runtime_stats (
  profile_id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  torrent_count INTEGER DEFAULT 0,
  total_size_bytes INTEGER DEFAULT 0,
  completed_bytes INTEGER DEFAULT 0,
  downloaded_bytes INTEGER DEFAULT 0,
  uploaded_bytes INTEGER DEFAULT 0,
  active_count INTEGER DEFAULT 0,
  seeding_count INTEGER DEFAULT 0,
  downloading_count INTEGER DEFAULT 0,
  stopped_count INTEGER DEFAULT 0,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id),
  FOREIGN KEY(profile_id) REFERENCES rtorrent_profiles(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_profile_runtime_stats_user ON profile_runtime_stats(user_id, profile_id);
