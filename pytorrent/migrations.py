from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
MIGRATION_STATE_FILE = "state.json"
MIGRATION_FILE_RE = re.compile(r"^(?P<version>\d{4,})_(?P<name>[a-z0-9_]+)\.sql$")
APPLIED_IF_RE = re.compile(r"^\s*--\s*pytorrent:applied-if\s+(.+?)\s*$", re.MULTILINE)


class DatabaseMigrationError(RuntimeError):
    """Raised when the database cannot be migrated safely with the available migration set."""


@dataclass(frozen=True)
class MigrationFile:
    version: int
    name: str
    path: Path
    checksum_sha256: str


@dataclass(frozen=True)
class MigrationState:
    latest_version: int
    retired_through: int


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_value(row: sqlite3.Row | dict[str, object] | tuple[object, ...], key: str, index: int) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return row[index]  # type: ignore[index]


def _load_migration_state() -> MigrationState:
    # Note: Lifecycle metadata lives beside the SQL files so migrations can be retired without embedding their history in Python code.
    state_path = MIGRATIONS_DIR / MIGRATION_STATE_FILE
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        latest_version = int(raw["latest_version"])
        retired_through = int(raw.get("retired_through", 0))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise DatabaseMigrationError(f"Invalid migration state file: {state_path}") from exc
    if latest_version < 0 or retired_through < 0 or retired_through > latest_version:
        raise DatabaseMigrationError("Migration state has an invalid version range")
    return MigrationState(latest_version=latest_version, retired_through=retired_through)


def _discover_migrations() -> dict[int, MigrationFile]:
    migrations: dict[int, MigrationFile] = {}
    if not MIGRATIONS_DIR.is_dir():
        raise DatabaseMigrationError(f"Migration directory is missing: {MIGRATIONS_DIR}")
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = MIGRATION_FILE_RE.fullmatch(path.name)
        if not match:
            continue
        version = int(match.group("version"))
        if version <= 0:
            raise DatabaseMigrationError(f"Migration version must be positive: {path.name}")
        if version in migrations:
            raise DatabaseMigrationError(f"Duplicate migration version {version:04d}")
        content = path.read_bytes()
        migrations[version] = MigrationFile(
            version=version,
            name=match.group("name"),
            path=path,
            checksum_sha256=hashlib.sha256(content).hexdigest(),
        )
    return migrations


def _ensure_migration_table(conn: sqlite3.Connection) -> None:
    # Note: Applied versions are durable database state; deleting an old SQL file never erases proof that the database already passed it.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY CHECK(version > 0),
          name TEXT NOT NULL CHECK(length(trim(name)) > 0),
          checksum_sha256 TEXT,
          source TEXT NOT NULL DEFAULT 'file' CHECK(source IN ('file', 'baseline')),
          applied_at TEXT NOT NULL
        )
        """
    )


def _applied_migrations(conn: sqlite3.Connection) -> dict[int, dict[str, object]]:
    rows = conn.execute(
        "SELECT version, name, checksum_sha256, source, applied_at FROM schema_migrations ORDER BY version"
    ).fetchall()
    result: dict[int, dict[str, object]] = {}
    for row in rows:
        version = int(_row_value(row, "version", 0))
        result[version] = {
            "name": str(_row_value(row, "name", 1) or ""),
            "checksum_sha256": _row_value(row, "checksum_sha256", 2),
            "source": str(_row_value(row, "source", 3) or ""),
            "applied_at": str(_row_value(row, "applied_at", 4) or ""),
        }
    return result


def _record_baseline(conn: sqlite3.Connection, version: int, name: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, name, checksum_sha256, source, applied_at) VALUES(?,?,?,?,?)",
        (version, name, None, "baseline", _utcnow()),
    )


def _migration_is_already_present(conn: sqlite3.Connection, migration: MigrationFile) -> bool:
    script = migration.path.read_text(encoding="utf-8")
    match = APPLIED_IF_RE.search(script)
    if not match:
        return False
    query = match.group(1).strip()
    try:
        row = conn.execute(query).fetchone()
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        # Note: Older databases may legitimately miss a table or column referenced by a later probe; that means the migration is not applied yet.
        if "no such table:" in message or "no such column:" in message:
            return False
        raise DatabaseMigrationError(f"Invalid applied-if check in {migration.path.name}") from exc
    except sqlite3.Error as exc:
        raise DatabaseMigrationError(f"Invalid applied-if check in {migration.path.name}") from exc
    if row is None:
        return False
    return bool(_row_value(row, "applied", 0))


def _bootstrap_migration_history(
    conn: sqlite3.Connection,
    state: MigrationState,
    migrations: dict[int, MigrationFile],
    *,
    current_schema_is_fresh: bool,
) -> None:
    applied = _applied_migrations(conn)
    if current_schema_is_fresh and not applied:
        # Note: Fresh databases are created from the current SCHEMA snapshot, so historical ALTER statements are recorded instead of replayed.
        for version in range(1, state.latest_version + 1):
            migration = migrations.get(version)
            _record_baseline(conn, version, migration.name if migration else f"retired_{version:04d}")
        return

    # Note: Pre-history databases use SQL-owned applied-if probes; Python stays migration-agnostic and only records effects already present.
    for version, migration in migrations.items():
        if version in applied:
            continue
        if _migration_is_already_present(conn, migration):
            _record_baseline(conn, version, migration.name)


def _strip_sql_comments(sql: str) -> str:
    without_blocks = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", "", without_blocks)


def _iter_sql_statements(script: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if _strip_sql_comments(statement).strip():
                statements.append(statement)
    if _strip_sql_comments(buffer).strip():
        raise DatabaseMigrationError("Migration SQL ends with an incomplete statement")
    return statements


def _execute_statement(conn: sqlite3.Connection, statement: str) -> None:
    try:
        conn.execute(statement)
    except sqlite3.OperationalError as exc:
        # Note: A previously interrupted legacy migration may have added only some columns; duplicate ADD COLUMN is safe to skip on retry.
        if "duplicate column name:" in str(exc).lower() and "ADD COLUMN" in statement.upper():
            return
        raise


def _apply_migration(conn: sqlite3.Connection, migration: MigrationFile) -> None:
    script = migration.path.read_text(encoding="utf-8")
    savepoint = f"pytorrent_migration_{migration.version:04d}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        for statement in _iter_sql_statements(script):
            _execute_statement(conn, statement)
        conn.execute(
            "INSERT INTO schema_migrations(version, name, checksum_sha256, source, applied_at) VALUES(?,?,?,?,?)",
            (migration.version, migration.name, migration.checksum_sha256, "file", _utcnow()),
        )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def _validate_available_path(
    state: MigrationState,
    migrations: dict[int, MigrationFile],
    applied: dict[int, dict[str, object]],
) -> None:
    newer = [version for version in applied if version > state.latest_version]
    if newer:
        raise DatabaseMigrationError(
            f"Database schema version {max(newer)} is newer than this pyTorrent build ({state.latest_version})"
        )

    for version in range(1, state.latest_version + 1):
        if version in applied:
            continue
        if version <= state.retired_through:
            raise DatabaseMigrationError(
                f"Database is too old for this build: migration {version:04d} was retired. "
                "Upgrade through an intermediate pyTorrent version first."
            )
        if version not in migrations:
            raise DatabaseMigrationError(f"Required migration file {version:04d} is missing")

    for version, migration in migrations.items():
        if version > state.latest_version:
            raise DatabaseMigrationError(
                f"Migration file {migration.path.name} is newer than migrations/{MIGRATION_STATE_FILE}"
            )
        row = applied.get(version)
        if not row or row.get("source") != "file":
            continue
        stored_name = str(row.get("name") or "")
        stored_checksum = str(row.get("checksum_sha256") or "")
        if stored_name != migration.name or stored_checksum != migration.checksum_sha256:
            raise DatabaseMigrationError(
                f"Applied migration {version:04d} no longer matches its recorded name/checksum"
            )


def run_database_migrations(conn: sqlite3.Connection, *, current_schema_is_fresh: bool = False) -> int:
    """Apply pending numbered SQL migrations and return the number executed in this run."""
    state = _load_migration_state()
    migrations = _discover_migrations()
    _ensure_migration_table(conn)
    _bootstrap_migration_history(
        conn,
        state,
        migrations,
        current_schema_is_fresh=current_schema_is_fresh,
    )
    applied = _applied_migrations(conn)
    _validate_available_path(state, migrations, applied)

    executed = 0
    for version in range(1, state.latest_version + 1):
        if version in applied:
            continue
        migration = migrations[version]
        try:
            _apply_migration(conn, migration)
        except Exception as exc:
            if isinstance(exc, DatabaseMigrationError):
                raise
            raise DatabaseMigrationError(f"Migration {migration.path.name} failed") from exc
        executed += 1
        applied[version] = _applied_migrations(conn)[version]
    return executed
