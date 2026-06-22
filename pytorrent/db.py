from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from .config import DB_PATH
from .migrations import run_database_migrations

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT,
  email TEXT,
  display_name TEXT,
  external_auth_provider TEXT,
  external_subject TEXT,
  role TEXT DEFAULT 'user',
  is_active INTEGER DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS user_profile_permissions (
  user_id INTEGER NOT NULL,
  profile_id INTEGER NOT NULL DEFAULT 0,
  access_level TEXT NOT NULL DEFAULT 'ro',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, profile_id),
  FOREIGN KEY(user_id) REFERENCES users(id)
);


CREATE TABLE IF NOT EXISTS api_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  token_hash TEXT NOT NULL,
  token_prefix TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user_active ON api_tokens(user_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_api_tokens_prefix ON api_tokens(token_prefix);
CREATE INDEX IF NOT EXISTS idx_api_tokens_active_user ON api_tokens(revoked_at, user_id);
CREATE INDEX IF NOT EXISTS idx_user_profile_permissions_user ON user_profile_permissions(user_id, profile_id);

CREATE TABLE IF NOT EXISTS user_preferences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  theme TEXT DEFAULT 'dark',
  bootstrap_theme TEXT DEFAULT 'default',
  font_family TEXT DEFAULT 'default',
  active_rtorrent_id INTEGER,
  keyboard_json TEXT,
  mobile_mode INTEGER DEFAULT 0,
  compact_torrent_list_enabled INTEGER DEFAULT 0,
  torrent_list_font_size INTEGER DEFAULT 13,
  footer_items_json TEXT,
  title_speed_enabled INTEGER DEFAULT 0,
  automation_toasts_enabled INTEGER DEFAULT 1,
  smart_queue_toasts_enabled INTEGER DEFAULT 1,
  easter_egg_enabled INTEGER DEFAULT 0,
  easter_egg_loading_image_url TEXT DEFAULT '',
  easter_egg_click_image_url TEXT DEFAULT '',
  interface_scale INTEGER DEFAULT 100,
  detail_panel_height INTEGER DEFAULT 255,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_user_preferences_user ON user_preferences(user_id);

CREATE TABLE IF NOT EXISTS profile_preferences (
  user_id INTEGER NOT NULL,
  profile_id INTEGER NOT NULL,
  table_columns_json TEXT,
  torrent_sort_json TEXT,
  active_filter TEXT DEFAULT 'all',
  peers_refresh_seconds INTEGER DEFAULT 0,
  port_check_enabled INTEGER DEFAULT 0,
  tracker_favicons_enabled INTEGER DEFAULT 0,
  reverse_dns_enabled INTEGER DEFAULT 0,
  sidebar_labels_expanded INTEGER DEFAULT 0,
  sidebar_shortcuts_expanded INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, profile_id),
  FOREIGN KEY(user_id) REFERENCES users(id),
  FOREIGN KEY(profile_id) REFERENCES rtorrent_profiles(id)
);
CREATE INDEX IF NOT EXISTS idx_profile_preferences_user_profile ON profile_preferences(user_id, profile_id);

CREATE TABLE IF NOT EXISTS rtorrent_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  scgi_url TEXT NOT NULL,
  is_default INTEGER DEFAULT 0,
  timeout_seconds INTEGER DEFAULT 5,
  max_parallel_jobs INTEGER DEFAULT 5,
  light_parallel_jobs INTEGER DEFAULT 4,
  light_job_timeout_seconds INTEGER DEFAULT 300,
  heavy_job_timeout_seconds INTEGER DEFAULT 7200,
  pending_job_timeout_seconds INTEGER DEFAULT 900,
  is_remote INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_rtorrent_profiles_user_default_name ON rtorrent_profiles(user_id, is_default, name COLLATE NOCASE);


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

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  profile_id INTEGER,
  action TEXT NOT NULL,
  payload_json TEXT,
  status TEXT NOT NULL,
  attempts INTEGER DEFAULT 0,
  max_attempts INTEGER DEFAULT 2,
  error TEXT,
  result_json TEXT,
  state_json TEXT,
  progress_current INTEGER DEFAULT 0,
  progress_total INTEGER DEFAULT 0,
  heartbeat_at TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_profile_status ON jobs(profile_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_profile_created ON jobs(profile_id, created_at);

CREATE TABLE IF NOT EXISTS disk_monitor_preferences (
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
CREATE INDEX IF NOT EXISTS idx_disk_monitor_preferences_owner ON disk_monitor_preferences(user_id);

CREATE TABLE IF NOT EXISTS labels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  profile_id INTEGER,
  name TEXT NOT NULL,
  color TEXT DEFAULT '#64748b',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id, profile_id, name)
);

CREATE TABLE IF NOT EXISTS ratio_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  profile_id INTEGER,
  name TEXT NOT NULL,
  min_ratio REAL DEFAULT 1.0,
  max_ratio REAL DEFAULT 2.0,
  seed_time_minutes INTEGER DEFAULT 0,
  min_seed_time_minutes INTEGER DEFAULT 0,
  ignore_private INTEGER DEFAULT 1,
  ignore_active_upload INTEGER DEFAULT 1,
  active_upload_min_bytes INTEGER DEFAULT 1024,
  move_path TEXT,
  set_label TEXT,
  action TEXT DEFAULT 'stop',
  enabled INTEGER DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id, profile_id, name)
);

CREATE TABLE IF NOT EXISTS rss_feeds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  enabled INTEGER DEFAULT 1,
  interval_minutes INTEGER DEFAULT 30,
  last_error TEXT,
  last_checked_at TEXT,
  next_check_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rss_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  pattern TEXT NOT NULL,
  exclude_pattern TEXT,
  min_size_mb INTEGER DEFAULT 0,
  max_size_mb INTEGER DEFAULT 0,
  category TEXT,
  quality TEXT,
  season INTEGER,
  episode INTEGER,
  save_path TEXT,
  label TEXT,
  start INTEGER DEFAULT 1,
  enabled INTEGER DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rss_feeds_profile_enabled_next ON rss_feeds(profile_id, enabled, next_check_at);
CREATE INDEX IF NOT EXISTS idx_rss_rules_profile_enabled ON rss_rules(profile_id, enabled);

CREATE TABLE IF NOT EXISTS rss_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL,
  feed_id INTEGER,
  rule_id INTEGER,
  title TEXT,
  link TEXT,
  status TEXT NOT NULL,
  message TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rss_history_profile_created ON rss_history(profile_id, created_at);
CREATE INDEX IF NOT EXISTS idx_rss_history_profile_status ON rss_history(profile_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rss_history_unique_success ON rss_history(profile_id, COALESCE(rule_id,0), link) WHERE status IN ('queued','added');

CREATE TABLE IF NOT EXISTS ratio_assignments (
  profile_id INTEGER NOT NULL,
  torrent_hash TEXT NOT NULL,
  group_id INTEGER,
  group_name TEXT,
  applied_at TEXT,
  last_status TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(profile_id, torrent_hash)
);

CREATE TABLE IF NOT EXISTS ratio_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  profile_id INTEGER NOT NULL,
  group_id INTEGER,
  group_name TEXT,
  torrent_hash TEXT NOT NULL,
  torrent_name TEXT,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT,
  details_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ratio_history_profile_created ON ratio_history(profile_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ratio_history_user_profile_id ON ratio_history(user_id, profile_id, id);
CREATE INDEX IF NOT EXISTS idx_ratio_assignments_profile_status ON ratio_assignments(profile_id, last_status);
CREATE INDEX IF NOT EXISTS idx_ratio_groups_user_profile_enabled ON ratio_groups(user_id, profile_id, enabled);
CREATE INDEX IF NOT EXISTS idx_ratio_groups_profile_enabled ON ratio_groups(profile_id, enabled, name);
CREATE INDEX IF NOT EXISTS idx_labels_profile_name ON labels(profile_id, name);

CREATE TABLE IF NOT EXISTS app_backups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  backup_type TEXT DEFAULT 'app',
  profile_id INTEGER,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_backups_profile_type_created ON app_backups(profile_id, backup_type, created_at);
CREATE INDEX IF NOT EXISTS idx_app_backups_user_type_created ON app_backups(user_id, backup_type, created_at);

CREATE TABLE IF NOT EXISTS smart_queue_settings (
  profile_id INTEGER NOT NULL,
  enabled INTEGER DEFAULT 0,
  max_active_downloads INTEGER DEFAULT 5,
  stalled_seconds INTEGER DEFAULT 300,
  min_speed_bytes INTEGER DEFAULT 1024,
  min_seeds INTEGER DEFAULT 1,
  min_peers INTEGER DEFAULT 0,
  ignore_seed_peer INTEGER DEFAULT 0,
  ignore_speed INTEGER DEFAULT 0,
  manage_stopped INTEGER DEFAULT 0,
  cooldown_minutes INTEGER DEFAULT 10,
  last_run_at TEXT,
  refill_enabled INTEGER DEFAULT 1,
  refill_interval_minutes INTEGER DEFAULT 0,
  last_refill_at TEXT,
  surge_refill_enabled INTEGER DEFAULT 0,
  surge_refill_interval_minutes INTEGER DEFAULT 1440,
  surge_refill_batch_size INTEGER DEFAULT 2000,
  last_surge_refill_at TEXT,
  stop_batch_size INTEGER DEFAULT 50,
  start_grace_seconds INTEGER DEFAULT 900,
  protect_active_below_cap INTEGER DEFAULT 1,
  prefer_partial_progress INTEGER DEFAULT 1,
  auto_stop_idle INTEGER DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(profile_id)
);

CREATE TABLE IF NOT EXISTS smart_queue_stalled (
  profile_id INTEGER NOT NULL,
  torrent_hash TEXT NOT NULL,
  first_stalled_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  timer_key TEXT DEFAULT '',
  PRIMARY KEY(profile_id, torrent_hash)
);

CREATE TABLE IF NOT EXISTS smart_queue_start_grace (
  profile_id INTEGER NOT NULL,
  torrent_hash TEXT NOT NULL,
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(profile_id, torrent_hash)
);

CREATE TABLE IF NOT EXISTS smart_queue_exclusions (
  profile_id INTEGER NOT NULL,
  torrent_hash TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(profile_id, torrent_hash)
);

CREATE INDEX IF NOT EXISTS idx_smart_queue_exclusions_profile_created ON smart_queue_exclusions(profile_id, created_at);

CREATE TABLE IF NOT EXISTS smart_queue_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL,
  event TEXT NOT NULL,
  paused_count INTEGER DEFAULT 0,
  resumed_count INTEGER DEFAULT 0,
  checked_count INTEGER DEFAULT 0,
  details_json TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_smart_queue_history_profile_created ON smart_queue_history(profile_id, created_at);


CREATE TABLE IF NOT EXISTS smart_queue_auto_labels (
  profile_id INTEGER NOT NULL,
  torrent_hash TEXT NOT NULL,
  previous_label TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(profile_id, torrent_hash)
);

CREATE TABLE IF NOT EXISTS traffic_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id INTEGER NOT NULL,
  down_rate INTEGER DEFAULT 0,
  up_rate INTEGER DEFAULT 0,
  total_down INTEGER DEFAULT 0,
  total_up INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_traffic_history_profile_created ON traffic_history(profile_id, created_at);

CREATE TABLE IF NOT EXISTS profile_speed_limits (
  profile_id INTEGER PRIMARY KEY,
  down_limit INTEGER DEFAULT 0,
  up_limit INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(profile_id) REFERENCES rtorrent_profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS transfer_speed_peaks (
  profile_id INTEGER PRIMARY KEY,
  session_started_at TEXT NOT NULL,
  session_down_peak INTEGER DEFAULT 0,
  session_up_peak INTEGER DEFAULT 0,
  session_down_peak_at TEXT,
  session_up_peak_at TEXT,
  all_time_down_peak INTEGER DEFAULT 0,
  all_time_up_peak INTEGER DEFAULT 0,
  all_time_down_peak_at TEXT,
  all_time_up_peak_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(profile_id) REFERENCES rtorrent_profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS automation_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  profile_id INTEGER,
  name TEXT NOT NULL,
  enabled INTEGER DEFAULT 1,
  conditions_json TEXT NOT NULL,
  effects_json TEXT NOT NULL,
  cooldown_minutes INTEGER DEFAULT 60,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_automation_rules_profile_enabled ON automation_rules(profile_id, enabled);
CREATE INDEX IF NOT EXISTS idx_automation_rules_user_profile_enabled ON automation_rules(user_id, profile_id, enabled);
CREATE TABLE IF NOT EXISTS automation_rule_state (
  rule_id INTEGER NOT NULL,
  profile_id INTEGER NOT NULL,
  torrent_hash TEXT NOT NULL,
  condition_since_at TEXT,
  last_matched_at TEXT,
  last_applied_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(rule_id, profile_id, torrent_hash)
);
CREATE TABLE IF NOT EXISTS automation_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  profile_id INTEGER NOT NULL,
  rule_id INTEGER,
  torrent_hash TEXT,
  torrent_name TEXT,
  rule_name TEXT,
  actions_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_automation_history_profile_created ON automation_history(profile_id, created_at);
CREATE INDEX IF NOT EXISTS idx_automation_history_user_profile_created ON automation_history(user_id, profile_id, created_at);

CREATE TABLE IF NOT EXISTS rtorrent_config_overrides (
  profile_id INTEGER NOT NULL,
  key TEXT NOT NULL,
  value TEXT,
  baseline_value TEXT,
  apply_on_start INTEGER DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(profile_id, key)
);
CREATE INDEX IF NOT EXISTS idx_rtorrent_config_overrides_profile ON rtorrent_config_overrides(profile_id, apply_on_start);

CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS poller_settings (
  profile_id INTEGER PRIMARY KEY,
  settings_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(profile_id) REFERENCES rtorrent_profiles(id)
);


CREATE TABLE IF NOT EXISTS download_plan_settings (
  user_id INTEGER NOT NULL,
  profile_id INTEGER NOT NULL,
  settings_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, profile_id)
);
CREATE INDEX IF NOT EXISTS idx_download_plan_settings_profile ON download_plan_settings(profile_id, updated_at);

CREATE TABLE IF NOT EXISTS download_plan_paused (
  profile_id INTEGER NOT NULL,
  torrent_hash TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(profile_id, torrent_hash)
);
CREATE INDEX IF NOT EXISTS idx_download_plan_paused_profile ON download_plan_paused(profile_id, updated_at);

CREATE TABLE IF NOT EXISTS torrent_stats_cache (
  profile_id INTEGER PRIMARY KEY,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  updated_epoch REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tracker_summary_cache (
  profile_id INTEGER NOT NULL,
  torrent_hash TEXT NOT NULL,
  trackers_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  updated_epoch REAL DEFAULT 0,
  PRIMARY KEY(profile_id, torrent_hash)
);
CREATE INDEX IF NOT EXISTS idx_tracker_summary_cache_profile ON tracker_summary_cache(profile_id, updated_epoch);


CREATE TABLE IF NOT EXISTS operation_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  profile_id INTEGER,
  event_type TEXT NOT NULL,
  severity TEXT DEFAULT 'info',
  source TEXT DEFAULT 'system',
  torrent_hash TEXT,
  torrent_name TEXT,
  action TEXT,
  message TEXT NOT NULL,
  details_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operation_logs_profile_created ON operation_logs(profile_id, created_at);
CREATE INDEX IF NOT EXISTS idx_operation_logs_user_profile_created ON operation_logs(user_id, profile_id, created_at);
CREATE INDEX IF NOT EXISTS idx_operation_logs_event_type ON operation_logs(event_type, created_at);

CREATE TABLE IF NOT EXISTS operation_log_settings (
  user_id INTEGER NOT NULL,
  profile_id INTEGER NOT NULL DEFAULT 0,
  retention_mode TEXT DEFAULT 'days',
  retention_days INTEGER DEFAULT 30,
  retention_lines INTEGER DEFAULT 5000,
  retention_interval_hours INTEGER DEFAULT 24,
  job_retention_mode TEXT DEFAULT 'days',
  job_retention_days INTEGER DEFAULT 7,
  job_retention_lines INTEGER DEFAULT 2000,
  job_retention_interval_hours INTEGER DEFAULT 24,
  job_last_retention_run_at TEXT,
  job_last_retention_deleted INTEGER DEFAULT 0,
  operation_retention_mode TEXT DEFAULT 'days',
  operation_retention_days INTEGER DEFAULT 30,
  operation_retention_lines INTEGER DEFAULT 5000,
  operation_retention_interval_hours INTEGER DEFAULT 24,
  operation_last_retention_run_at TEXT,
  operation_last_retention_deleted INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, profile_id)
);
CREATE INDEX IF NOT EXISTS idx_operation_log_settings_profile ON operation_log_settings(profile_id, updated_at);
CREATE TABLE IF NOT EXISTS tracker_favicon_cache (
  domain TEXT PRIMARY KEY,
  source_url TEXT,
  file_path TEXT,
  mime_type TEXT,
  updated_at TEXT NOT NULL,
  updated_epoch REAL DEFAULT 0,
  error TEXT
);
"""


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the current database schema definition."""
    conn.executescript(SCHEMA)


def seed_default_user(conn: sqlite3.Connection) -> None:
    """Ensure the built-in admin user and default preferences exist."""
    now = utcnow()
    conn.execute(
        "INSERT OR IGNORE INTO users(id, username, password_hash, role, is_active, created_at, updated_at) VALUES(1, 'default', NULL, 'admin', 1, ?, ?)",
        (now, now),
    )
    conn.execute(
        "UPDATE users SET role=COALESCE(role, 'admin'), is_active=COALESCE(is_active, 1), updated_at=COALESCE(updated_at, ?) WHERE id=1",
        (now,),
    )
    pref = conn.execute("SELECT id FROM user_preferences WHERE user_id=1").fetchone()
    if not pref:
        conn.execute(
            "INSERT INTO user_preferences(user_id, theme, created_at, updated_at) VALUES(1, 'dark', ?, ?)",
            (now, now),
        )


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Initialize SQLite, applying the current schema and idempotent migrations."""
    with connect() as conn:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        create_schema(conn)
        run_database_migrations(conn)
        seed_default_user(conn)
    try:
        from .services.auth import ensure_admin_user

        ensure_admin_user()
    except Exception:
        pass


def default_user_id() -> int:
    return 1
