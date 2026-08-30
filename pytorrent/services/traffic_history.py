from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any
from ..config import TRAFFIC_HISTORY_RETENTION_DAYS
from ..db import connect, utcnow
from . import retention

_LAST_WRITE: dict[int, float] = {}
WRITE_EVERY_SECONDS = 60
MINUTE_RESOLUTION_HOURS = 3
HOURLY_RESOLUTION_HOURS = 24


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


def _floor_hour(value: datetime) -> datetime:
    # Note: Hourly compaction starts only after a complete chart hour is outside the minute-resolution window.
    return value.replace(minute=0, second=0, microsecond=0)


def _floor_day(value: datetime) -> datetime:
    # Note: Daily compaction keeps the entire calendar day that can still intersect the 24-hour chart range.
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _compact_tier(conn: Any, cutoff: datetime, bucket_format: str) -> dict[str, int]:
    # Note: One persisted row replaces a completed bucket while preserving weighted speed averages and exact transfer deltas across counter resets.
    cutoff_s = cutoff.isoformat(timespec="seconds")
    conn.execute("DROP TABLE IF EXISTS temp._traffic_history_rollup")
    conn.execute(
        """
        CREATE TEMP TABLE _traffic_history_rollup (
          keep_id INTEGER PRIMARY KEY,
          profile_id INTEGER NOT NULL,
          bucket_key TEXT NOT NULL,
          avg_down_rate INTEGER NOT NULL,
          avg_up_rate INTEGER NOT NULL,
          downloaded INTEGER NOT NULL,
          uploaded INTEGER NOT NULL,
          sample_count INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO _traffic_history_rollup(
          keep_id, profile_id, bucket_key, avg_down_rate, avg_up_rate,
          downloaded, uploaded, sample_count
        )
        WITH ordered AS (
          SELECT
            id,
            profile_id,
            down_rate,
            up_rate,
            total_down,
            total_up,
            downloaded,
            uploaded,
            CASE WHEN COALESCE(sample_count, 1) > 0 THEN COALESCE(sample_count, 1) ELSE 1 END AS weight,
            created_at,
            LAG(total_down) OVER (PARTITION BY profile_id ORDER BY created_at, id) AS prev_total_down,
            LAG(total_up) OVER (PARTITION BY profile_id ORDER BY created_at, id) AS prev_total_up
          FROM traffic_history
        ), candidates AS (
          SELECT *, strftime(?, created_at) AS bucket_key
          FROM ordered
          WHERE created_at < ?
        )
        SELECT
          MAX(id) AS keep_id,
          profile_id,
          bucket_key,
          CAST(ROUND(SUM(CAST(down_rate AS REAL) * weight) / SUM(weight)) AS INTEGER) AS avg_down_rate,
          CAST(ROUND(SUM(CAST(up_rate AS REAL) * weight) / SUM(weight)) AS INTEGER) AS avg_up_rate,
          CAST(SUM(
            CASE
              WHEN downloaded IS NOT NULL THEN CASE WHEN downloaded > 0 THEN downloaded ELSE 0 END
              WHEN prev_total_down IS NOT NULL AND total_down >= prev_total_down THEN total_down - prev_total_down
              ELSE 0
            END
          ) AS INTEGER) AS downloaded,
          CAST(SUM(
            CASE
              WHEN uploaded IS NOT NULL THEN CASE WHEN uploaded > 0 THEN uploaded ELSE 0 END
              WHEN prev_total_up IS NOT NULL AND total_up >= prev_total_up THEN total_up - prev_total_up
              ELSE 0
            END
          ) AS INTEGER) AS uploaded,
          CAST(SUM(weight) AS INTEGER) AS sample_count
        FROM candidates
        WHERE bucket_key IS NOT NULL
        GROUP BY profile_id, bucket_key
        HAVING COUNT(*) > 1
        """,
        (bucket_format, cutoff_s),
    )
    conn.execute("CREATE INDEX _traffic_history_rollup_bucket ON _traffic_history_rollup(profile_id, bucket_key)")
    bucket_row = conn.execute("SELECT COUNT(*) AS count FROM _traffic_history_rollup").fetchone()
    bucket_count = int(_row_value(bucket_row, "count", 0, 0) or 0)
    if not bucket_count:
        conn.execute("DROP TABLE temp._traffic_history_rollup")
        return {"buckets": 0, "deleted": 0}

    conn.execute(
        """
        UPDATE traffic_history
        SET
          down_rate=(SELECT avg_down_rate FROM _traffic_history_rollup WHERE keep_id=traffic_history.id),
          up_rate=(SELECT avg_up_rate FROM _traffic_history_rollup WHERE keep_id=traffic_history.id),
          downloaded=(SELECT downloaded FROM _traffic_history_rollup WHERE keep_id=traffic_history.id),
          uploaded=(SELECT uploaded FROM _traffic_history_rollup WHERE keep_id=traffic_history.id),
          sample_count=(SELECT sample_count FROM _traffic_history_rollup WHERE keep_id=traffic_history.id)
        WHERE id IN (SELECT keep_id FROM _traffic_history_rollup)
        """
    )
    cur = conn.execute(
        """
        DELETE FROM traffic_history
        WHERE created_at < ?
          AND EXISTS (
            SELECT 1
            FROM _traffic_history_rollup AS rollup
            WHERE rollup.profile_id=traffic_history.profile_id
              AND rollup.bucket_key=strftime(?, traffic_history.created_at)
              AND rollup.keep_id<>traffic_history.id
          )
        """,
        (cutoff_s, bucket_format),
    )
    deleted = int(cur.rowcount or 0)
    conn.execute("DROP TABLE temp._traffic_history_rollup")
    return {"buckets": bucket_count, "deleted": deleted}


def compact_for_charts(conn: Any, now: datetime | None = None) -> dict[str, int]:
    # Note: Housekeeping keeps only the finest persisted resolution still required by the History chart ranges.
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    hourly_cutoff = _floor_hour(current - timedelta(hours=MINUTE_RESOLUTION_HOURS))
    daily_cutoff = _floor_day(current - timedelta(hours=HOURLY_RESOLUTION_HOURS))
    hourly = _compact_tier(conn, hourly_cutoff, "%Y-%m-%dT%H")
    daily = _compact_tier(conn, daily_cutoff, "%Y-%m-%d")
    return {
        "hourly_buckets": hourly["buckets"],
        "daily_buckets": daily["buckets"],
        "deleted": hourly["deleted"] + daily["deleted"],
    }


def history(profile_id: int, range_name: str = "7d") -> dict[str, Any]:
    # Note: History reads understand both raw samples and persisted rollups so the API contract stays unchanged after housekeeping.
    cutoff = _range_to_cutoff(range_name)
    bucket = _bucket_for(range_name)
    cutoff_s = cutoff.isoformat(timespec="seconds")
    bucket_name = "minute" if range_name in {"15m", "1h", "3h"} else ("hour" if range_name in {"6h", "24h"} else "day")
    with connect() as conn:
        raw = conn.execute(
            """
            SELECT down_rate, up_rate, total_down, total_up, downloaded, uploaded, sample_count, created_at
            FROM traffic_history
            WHERE profile_id=? AND created_at >= ?
            ORDER BY created_at ASC, id ASC
            """,
            (int(profile_id), cutoff_s),
        ).fetchall()

    rows_by_bucket: dict[str, dict[str, Any]] = {}
    prev_down = prev_up = None
    for r in raw:
        created = str(_row_value(r, "created_at", 7, ""))
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
        stored_downloaded = _row_value(r, "downloaded", 4, None)
        stored_uploaded = _row_value(r, "uploaded", 5, None)
        sample_count = max(1, int(_row_value(r, "sample_count", 6, 1) or 1))
        item["avg_down_rate"] += down_rate * sample_count
        item["avg_up_rate"] += up_rate * sample_count
        item["samples"] += sample_count
        if stored_downloaded is not None:
            item["downloaded"] += max(0, int(stored_downloaded or 0))
        elif prev_down is not None and total_down >= prev_down:
            item["downloaded"] += total_down - prev_down
        if stored_uploaded is not None:
            item["uploaded"] += max(0, int(stored_uploaded or 0))
        elif prev_up is not None and total_up >= prev_up:
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
