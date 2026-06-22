from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any
from ..config import TRAFFIC_HISTORY_RETENTION_DAYS
from ..db import connect, utcnow
from . import retention

_LAST_WRITE: dict[int, float] = {}
WRITE_EVERY_SECONDS = 60


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def record(profile_id: int, down_rate: int = 0, up_rate: int = 0, total_down: int = 0, total_up: int = 0, force: bool = False) -> None:
    """Store compact transfer samples. One sample per minute per profile keeps SQLite small."""
    profile_id = int(profile_id)
    now_ts = _now_ts()
    if not force and now_ts - _LAST_WRITE.get(profile_id, 0.0) < WRITE_EVERY_SECONDS:
        return
    _LAST_WRITE[profile_id] = now_ts
    with connect() as conn:
        conn.execute(
            "INSERT INTO traffic_history(profile_id,down_rate,up_rate,total_down,total_up,created_at) VALUES(?,?,?,?,?,?)",
            (profile_id, int(down_rate or 0), int(up_rate or 0), int(total_down or 0), int(total_up or 0), utcnow()),
        )
    retention.cleanup()


def _range_to_cutoff(range_name: str) -> datetime:
    now = datetime.now(timezone.utc)
    if range_name == "15m":
        return now - timedelta(minutes=15)
    if range_name == "1h":
        return now - timedelta(hours=1)
    if range_name == "3h":
        return now - timedelta(hours=3)
    if range_name == "6h":
        return now - timedelta(hours=6)
    if range_name == "24h":
        return now - timedelta(hours=24)
    if range_name == "30d":
        return now - timedelta(days=30)
    if range_name == "90d":
        return now - timedelta(days=90)
    return now - timedelta(days=7)


def _bucket_for(range_name: str) -> str:
    if range_name in {"15m", "1h", "3h"}:
        return "%Y-%m-%d %H:%M"
    if range_name in {"6h", "24h"}:
        return "%Y-%m-%d %H:00"
    return "%Y-%m-%d"


def _row_value(row: Any, key: str, index: int, default: Any = 0) -> Any:
    # connect() uses dict_factory, so SQLite rows are dicts. The fallback keeps
    # this function compatible with tuple/list rows in tests or future refactors.
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return default


def history(profile_id: int, range_name: str = "7d") -> dict[str, Any]:
    cutoff = _range_to_cutoff(range_name)
    bucket = _bucket_for(range_name)
    cutoff_s = cutoff.isoformat(timespec="seconds")
    bucket_name = "minute" if range_name in {"15m", "1h", "3h"} else ("hour" if range_name in {"6h", "24h"} else "day")
    with connect() as conn:
        raw = conn.execute(
            """
            SELECT down_rate, up_rate, total_down, total_up, created_at
            FROM traffic_history
            WHERE profile_id=? AND created_at >= ?
            ORDER BY created_at ASC
            """,
            (int(profile_id), cutoff_s),
        ).fetchall()

    rows_by_bucket: dict[str, dict[str, Any]] = {}
    prev_down = prev_up = None
    for r in raw:
        created = str(_row_value(r, "created_at", 4, ""))
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except Exception:
            continue
        b = dt.strftime(bucket)
        item = rows_by_bucket.setdefault(b, {"bucket": b, "avg_down_rate": 0, "avg_up_rate": 0, "downloaded": 0, "uploaded": 0, "samples": 0})
        down_rate = int(_row_value(r, "down_rate", 0, 0) or 0)
        up_rate = int(_row_value(r, "up_rate", 1, 0) or 0)
        total_down = int(_row_value(r, "total_down", 2, 0) or 0)
        total_up = int(_row_value(r, "total_up", 3, 0) or 0)
        item["avg_down_rate"] += down_rate
        item["avg_up_rate"] += up_rate
        item["samples"] += 1
        if prev_down is not None and total_down >= prev_down:
            item["downloaded"] += total_down - prev_down
        if prev_up is not None and total_up >= prev_up:
            item["uploaded"] += total_up - prev_up
        prev_down, prev_up = total_down, total_up

    rows = []
    for item in rows_by_bucket.values():
        samples = max(1, int(item["samples"] or 1))
        item["avg_down_rate"] = round(item["avg_down_rate"] / samples)
        item["avg_up_rate"] = round(item["avg_up_rate"] / samples)
        rows.append(item)
    rows.sort(key=lambda x: x["bucket"])
    return {"range": range_name, "bucket": bucket_name, "retention_days": TRAFFIC_HISTORY_RETENTION_DAYS, "rows": rows}
