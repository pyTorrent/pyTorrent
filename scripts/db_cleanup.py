#!/usr/bin/env python3
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path("/opt/pyTorrent/data/pytorrent.sqlite3")

DROP_COLUMNS = {
    "rss_feeds": [
        "user_id",
    ],
    "rss_rules": [
        "user_id",
    ],
    "rss_history": [
        "user_id",
    ],
    "smart_queue_settings": [
        "user_id",
    ],
    "smart_queue_exclusions": [
        "user_id",
    ],
    "smart_queue_history": [
        "user_id",
    ],
    "rtorrent_config_overrides": [
        "user_id",
    ],
    "user_preferences": [
        "table_columns_json",
        "peers_refresh_seconds",
        "port_check_enabled",
        "tracker_favicons_enabled",
        "reverse_dns_enabled",
        "disk_monitor_paths_json",
        "disk_monitor_mode",
        "disk_monitor_selected_path",
        "disk_monitor_stop_enabled",
        "disk_monitor_stop_threshold",
        "torrent_sort_json",
        "active_filter",
    ],
}

DROP_INDEXES = [
    "idx_rss_feeds_user_profile",
    "idx_rss_rules_user_profile",
    "idx_rss_history_user_profile",
    "idx_smart_queue_settings_user_profile",
    "idx_smart_queue_exclusions_user_profile",
    "idx_smart_queue_history_user_profile",
    "idx_rtorrent_config_overrides_user_profile",
]

EXPECTED_PROFILE_TABLES = {
    "rss_feeds": ["profile_id"],
    "rss_rules": ["profile_id"],
    "rss_history": ["profile_id"],
    "smart_queue_settings": ["profile_id"],
    "smart_queue_exclusions": ["profile_id"],
    "smart_queue_history": ["profile_id"],
    "rtorrent_config_overrides": ["profile_id"],
}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def index_exists(conn: sqlite3.Connection, index: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (index,),
    ).fetchone()
    return row is not None


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def validate_profile_tables(conn: sqlite3.Connection) -> None:
    print("Checking required profile scoped tables...")

    for table, required_columns in EXPECTED_PROFILE_TABLES.items():
        if not table_exists(conn, table):
            print(f"SKIP table missing: {table}")
            continue

        table_columns = columns(conn, table)

        for column in required_columns:
            if column not in table_columns:
                raise RuntimeError(
                    f"Unsafe cleanup: table {table} does not contain required column {column}"
                )

        print(f"OK {table}: has {', '.join(required_columns)}")


def drop_indexes(conn: sqlite3.Connection) -> None:
    print("\nDropping obsolete indexes if present...")

    for index in DROP_INDEXES:
        if not index_exists(conn, index):
            print(f"SKIP index missing: {index}")
            continue

        conn.execute(f"DROP INDEX {quote_ident(index)}")
        print(f"DROPPED index: {index}")


def drop_obsolete_columns(conn: sqlite3.Connection) -> None:
    print("\nDropping obsolete columns if present...")

    for table, obsolete_columns in DROP_COLUMNS.items():
        if not table_exists(conn, table):
            print(f"SKIP table missing: {table}")
            continue

        current_columns = columns(conn, table)

        for column in obsolete_columns:
            if column not in current_columns:
                print(f"SKIP column missing: {table}.{column}")
                continue

            try:
                conn.execute(
                    f"ALTER TABLE {quote_ident(table)} DROP COLUMN {quote_ident(column)}"
                )
                print(f"DROPPED column: {table}.{column}")
                current_columns.remove(column)
            except sqlite3.OperationalError as exc:
                print(f"FAILED column: {table}.{column} -> {exc}")
                print("This usually means the column is used by an index, constraint, or old SQLite version.")


def vacuum(conn: sqlite3.Connection) -> None:
    print("\nRunning VACUUM...")
    conn.execute("VACUUM")
    print("VACUUM done.")


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found: {DB_PATH}")

    backup_path = DB_PATH.with_suffix(
        DB_PATH.suffix + f".cleanup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.bak"
    )

    print(f"Database: {DB_PATH}")
    print(f"Backup:   {backup_path}")

    shutil.copy2(DB_PATH, backup_path)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA foreign_keys = OFF")

        validate_profile_tables(conn)

        conn.execute("BEGIN")
        drop_indexes(conn)
        drop_obsolete_columns(conn)
        conn.commit()

        conn.execute("PRAGMA foreign_keys = ON")

        vacuum(conn)

        print("\nCleanup completed successfully.")
        print(f"Backup saved as: {backup_path}")

    except Exception:
        conn.rollback()
        print("\nCleanup failed. Database rollback completed.")
        print(f"Backup is available at: {backup_path}")
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()
