from __future__ import annotations
from datetime import datetime, timedelta, timezone
from ..config import JOBS_RETENTION_DAYS, LOG_RETENTION_DAYS, SMART_QUEUE_HISTORY_RETENTION_DAYS, TRAFFIC_HISTORY_RETENTION_DAYS
from ..db import connect

_LAST_CLEANUP = 0.0
CLEANUP_EVERY_SECONDS = 3600


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(1, int(days or 1)))).isoformat(timespec="seconds")


def _table_exists(conn, table: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return bool(row)


def cleanup(force: bool = False) -> dict[str, int]:
    global _LAST_CLEANUP
    now_ts = datetime.now(timezone.utc).timestamp()
    if not force and now_ts - _LAST_CLEANUP < CLEANUP_EVERY_SECONDS:
        return {}
    _LAST_CLEANUP = now_ts

    deleted: dict[str, int] = {}
    with connect() as conn:
        targets = {
            "traffic_history": ("created_at", TRAFFIC_HISTORY_RETENTION_DAYS),
            "smart_queue_history": ("created_at", SMART_QUEUE_HISTORY_RETENTION_DAYS),
            # Note: Automation history follows Smart Queue retention; rules and rule state are never deleted here.
            "automation_history": ("created_at", SMART_QUEUE_HISTORY_RETENTION_DAYS),
            "jobs": ("updated_at", JOBS_RETENTION_DAYS),
            "logs": ("created_at", LOG_RETENTION_DAYS),
        }
        for table, (column, days) in targets.items():
            if not _table_exists(conn, table):
                continue
            if table == "jobs":
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE {column} < ? AND status IN ('done','failed','cancelled')",
                    (_cutoff(days),),
                )
            else:
                cur = conn.execute(f"DELETE FROM {table} WHERE {column} < ?", (_cutoff(days),))
            deleted[table] = int(cur.rowcount or 0)
    return deleted
