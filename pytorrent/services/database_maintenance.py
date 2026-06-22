from __future__ import annotations
import shutil
import sqlite3
import threading
import time
from typing import Any
from ..config import DB_PATH

_VACUUM_LOCK = threading.Lock()
MIN_DISK_HEADROOM_BYTES = 128 * 1024 * 1024


def _human_size(value: int | float | None) -> str:
    size = float(value or 0)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.2f} {units[idx]}"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=60, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def _pragma_int(conn: sqlite3.Connection, pragma_name: str) -> int:
    row = conn.execute(f"PRAGMA {pragma_name}").fetchone()
    if row is None:
        return 0
    return int(row[0] or 0)


def database_status() -> dict[str, Any]:
    size_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    wal_path = DB_PATH.with_name(DB_PATH.name + "-wal")
    wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
    page_size = 0
    page_count = 0
    freelist_count = 0
    error = None
    if DB_PATH.exists():
        try:
            with _connect() as conn:
                page_size = _pragma_int(conn, "page_size")
                page_count = _pragma_int(conn, "page_count")
                freelist_count = _pragma_int(conn, "freelist_count")
        except Exception as exc:
            error = str(exc)
    free_bytes = int(page_size * freelist_count)
    logical_bytes = int(page_size * page_count)
    free_ratio = (free_bytes / logical_bytes) if logical_bytes else 0.0
    try:
        disk = shutil.disk_usage(str(DB_PATH.parent))
        disk_free = int(disk.free)
    except Exception:
        disk_free = 0
    return {
        "path": str(DB_PATH),
        "size": int(size_bytes),
        "size_h": _human_size(size_bytes),
        "wal_size": int(wal_bytes),
        "wal_size_h": _human_size(wal_bytes),
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "free_inside": free_bytes,
        "free_inside_h": _human_size(free_bytes),
        "free_ratio": round(free_ratio, 4),
        "free_ratio_percent": round(free_ratio * 100, 2),
        "disk_free": disk_free,
        "disk_free_h": _human_size(disk_free),
        "vacuum_running": _VACUUM_LOCK.locked(),
        "error": error,
    }


def _checkpoint_truncate(conn: sqlite3.Connection) -> dict[str, int] | None:
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if row is None:
            return None
        return {"busy": int(row[0] or 0), "log": int(row[1] or 0), "checkpointed": int(row[2] or 0)}
    except sqlite3.DatabaseError:
        return None


def vacuum_database(force: bool = False) -> dict[str, Any]:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")
    if not _VACUUM_LOCK.acquire(blocking=False):
        raise RuntimeError("Database vacuum is already running")
    try:
        before = database_status()
        required_free = int(before.get("size") or 0) + MIN_DISK_HEADROOM_BYTES
        available_free = int(before.get("disk_free") or 0)
        if available_free and available_free < required_free:
            raise RuntimeError(
                "Not enough free disk space for VACUUM: "
                f"need about {_human_size(required_free)}, have {_human_size(available_free)}"
            )
        if not force and int(before.get("free_inside") or 0) <= 0:
            return {"ok": True, "skipped": True, "reason": "No free pages inside SQLite database", "before": before, "after": before}
        started = time.perf_counter()
        with _connect() as conn:
            checkpoint_before = _checkpoint_truncate(conn)
            conn.execute("VACUUM")
            checkpoint_after = _checkpoint_truncate(conn)
        after = database_status()
        return {
            "ok": True,
            "skipped": False,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "checkpoint_before": checkpoint_before,
            "checkpoint_after": checkpoint_after,
            "before": before,
            "after": after,
            "reclaimed": max(0, int(before.get("size") or 0) - int(after.get("size") or 0)),
            "reclaimed_h": _human_size(max(0, int(before.get("size") or 0) - int(after.get("size") or 0))),
        }
    finally:
        _VACUUM_LOCK.release()
