from __future__ import annotations
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

Migration = Callable[[sqlite3.Connection], bool]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_value(row: sqlite3.Row | dict[str, object] | tuple[object, ...], key: str, index: int) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return row[index]  # type: ignore[index]


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(_row_value(row, "name", 1)) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _primary_key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
    pk_columns = sorted(
        (
            (int(_row_value(row, "pk", 5) or 0), str(_row_value(row, "name", 1)))
            for row in columns
            if int(_row_value(row, "pk", 5) or 0)
        ),
        key=lambda item: item[0],
    )
    return [name for _, name in pk_columns]


def migrate_disk_monitor_preferences_to_profile_scope(conn: sqlite3.Connection) -> bool:
    if _primary_key_columns(conn, "disk_monitor_preferences") == ["profile_id"]:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_disk_monitor_preferences_owner ON disk_monitor_preferences(user_id)")
        return False

    now = _utcnow()
    conn.execute("DROP INDEX IF EXISTS idx_disk_monitor_preferences_owner")
    conn.execute("DROP TABLE IF EXISTS disk_monitor_preferences_new")
    conn.execute("DROP TABLE IF EXISTS disk_monitor_preferences_old_user_profile")
    conn.execute("""
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
        )
    """)
    conn.execute("""
        INSERT INTO disk_monitor_preferences_new(
          profile_id, user_id, paths_json, mode, selected_path, stop_enabled, stop_threshold, created_at, updated_at
        )
        SELECT profile_id, user_id, paths_json, mode, selected_path, stop_enabled, stop_threshold,
               COALESCE(created_at, ?), COALESCE(updated_at, ?)
        FROM (
          SELECT d.*,
                 ROW_NUMBER() OVER (
                   PARTITION BY profile_id
                   ORDER BY COALESCE(updated_at, created_at, '') DESC, user_id ASC
                 ) AS rn
          FROM disk_monitor_preferences d
          WHERE profile_id IS NOT NULL
        )
        WHERE rn = 1
    """, (now, now))
    conn.execute("ALTER TABLE disk_monitor_preferences RENAME TO disk_monitor_preferences_old_user_profile")
    conn.execute("ALTER TABLE disk_monitor_preferences_new RENAME TO disk_monitor_preferences")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_disk_monitor_preferences_owner ON disk_monitor_preferences(user_id)")
    return True


def migrate_profile_preferences_sidebar_columns(conn: sqlite3.Connection) -> bool:
    columns = _column_names(conn, "profile_preferences")
    changed = False
    if "sidebar_labels_expanded" not in columns:
        conn.execute("ALTER TABLE profile_preferences ADD COLUMN sidebar_labels_expanded INTEGER DEFAULT 0")
        changed = True
    if "sidebar_shortcuts_expanded" not in columns:
        conn.execute("ALTER TABLE profile_preferences ADD COLUMN sidebar_shortcuts_expanded INTEGER DEFAULT 0")
        changed = True
    return changed


def migrate_operation_log_split_retention(conn: sqlite3.Connection) -> bool:
    columns = _column_names(conn, "operation_log_settings")
    changed = False
    additions = {
        "retention_interval_hours": "INTEGER DEFAULT 24",
        "job_retention_mode": "TEXT DEFAULT 'days'",
        "job_retention_days": "INTEGER DEFAULT 7",
        "job_retention_lines": "INTEGER DEFAULT 2000",
        "job_retention_interval_hours": "INTEGER DEFAULT 24",
        "job_last_retention_run_at": "TEXT",
        "job_last_retention_deleted": "INTEGER DEFAULT 0",
        "operation_retention_mode": "TEXT DEFAULT 'days'",
        "operation_retention_days": "INTEGER DEFAULT 30",
        "operation_retention_lines": "INTEGER DEFAULT 5000",
        "operation_retention_interval_hours": "INTEGER DEFAULT 24",
        "operation_last_retention_run_at": "TEXT",
        "operation_last_retention_deleted": "INTEGER DEFAULT 0",
    }
    for name, ddl in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE operation_log_settings ADD COLUMN {name} {ddl}")
            changed = True
    if changed:
        conn.execute("""
            UPDATE operation_log_settings
            SET operation_retention_mode=COALESCE(operation_retention_mode, retention_mode, 'days'),
                operation_retention_days=COALESCE(operation_retention_days, retention_days, 30),
                operation_retention_lines=COALESCE(operation_retention_lines, retention_lines, 5000),
                operation_retention_interval_hours=COALESCE(operation_retention_interval_hours, retention_interval_hours, 24),
                job_retention_mode=COALESCE(job_retention_mode, 'days'),
                job_retention_days=COALESCE(job_retention_days, 7),
                job_retention_lines=COALESCE(job_retention_lines, 2000),
                job_retention_interval_hours=COALESCE(job_retention_interval_hours, retention_interval_hours, 24),
                updated_at=COALESCE(updated_at, ?)
        """, (_utcnow(),))
    return changed


def migrate_profile_speed_limits_table(conn: sqlite3.Connection) -> bool:
    existing = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='profile_speed_limits'").fetchone()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profile_speed_limits (
          profile_id INTEGER PRIMARY KEY,
          down_limit INTEGER DEFAULT 0,
          up_limit INTEGER DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(profile_id) REFERENCES rtorrent_profiles(id) ON DELETE CASCADE
        )
    """)
    return existing is None


def migrate_profile_runtime_stats_table(conn: sqlite3.Connection) -> bool:
    existing = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='profile_runtime_stats'").fetchone()
    conn.execute("""
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
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_profile_runtime_stats_user ON profile_runtime_stats(user_id, profile_id)")
    return existing is None


MIGRATIONS: tuple[Migration, ...] = (
    migrate_disk_monitor_preferences_to_profile_scope,
    migrate_profile_preferences_sidebar_columns,
    migrate_operation_log_split_retention,
    migrate_profile_speed_limits_table,
    migrate_profile_runtime_stats_table,
)


def run_database_migrations(conn: sqlite3.Connection) -> int:
    """Run idempotent database migrations and return how many changed the schema/data."""
    applied = 0
    for migration in MIGRATIONS:
        if migration(conn):
            applied += 1
    return applied
