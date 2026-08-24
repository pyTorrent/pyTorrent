-- Note: Persist the last small per-profile footer/system snapshot so profile switches never depend on browser storage or a live SCGI response.
-- pytorrent:applied-if SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='profile_status_cache') AS applied;
CREATE TABLE IF NOT EXISTS profile_status_cache (
  profile_id INTEGER PRIMARY KEY,
  status_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL,
  FOREIGN KEY(profile_id) REFERENCES rtorrent_profiles(id) ON DELETE CASCADE
);
