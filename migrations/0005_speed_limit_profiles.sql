-- Note: Reusable named speed-limit presets are owned by the application user.
-- pytorrent:applied-if SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'speed_limit_profiles') AS applied;
CREATE TABLE IF NOT EXISTS speed_limit_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  down_limit INTEGER DEFAULT 0,
  up_limit INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_speed_limit_profiles_user ON speed_limit_profiles(user_id, lower(name));
