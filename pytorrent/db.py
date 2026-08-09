from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .config import DB_PATH
from .migrations import run_database_migrations

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"


def load_schema() -> str:
    """Load the canonical schema snapshot used for fresh databases."""
    try:
        return SCHEMA_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Database schema file is unavailable: {SCHEMA_PATH}") from exc


def create_schema(conn: sqlite3.Connection) -> None:
    """Create objects from the current database schema snapshot."""
    conn.executescript(load_schema())


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
    """Initialize SQLite, applying the current schema and pending SQL migrations."""
    with connect() as conn:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        # Note: Fresh databases receive schema.sql first, so historical ALTER migrations are recorded as a baseline instead of replayed.
        current_schema_is_fresh = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='schema_migrations' LIMIT 1"
        ).fetchone() is None
        create_schema(conn)
        run_database_migrations(conn, current_schema_is_fresh=current_schema_is_fresh)
        seed_default_user(conn)
    try:
        from .services.auth import ensure_admin_user

        ensure_admin_user()
    except Exception:
        pass


def default_user_id() -> int:
    return 1
