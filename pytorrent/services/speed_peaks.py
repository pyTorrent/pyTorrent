from __future__ import annotations
import threading
from typing import Any
from ..db import connect, utcnow
from .rtorrent import human_rate

_SESSION_STARTED_AT = utcnow()
_CACHE: dict[int, dict[str, Any]] = {}
_LOADED = False
_LOCK = threading.Lock()


def _empty_peak(profile_id: int, all_time: dict[str, Any] | None = None) -> dict[str, Any]:
    # Note: One in-memory structure keeps the current session and all-time record for the rTorrent profile.
    all_time = all_time or {}
    return {
        "profile_id": int(profile_id),
        "session_started_at": _SESSION_STARTED_AT,
        "session_down_peak": 0,
        "session_up_peak": 0,
        "session_down_peak_at": None,
        "session_up_peak_at": None,
        "all_time_down_peak": int(all_time.get("all_time_down_peak") or 0),
        "all_time_up_peak": int(all_time.get("all_time_up_peak") or 0),
        "all_time_down_peak_at": all_time.get("all_time_down_peak_at"),
        "all_time_up_peak_at": all_time.get("all_time_up_peak_at"),
    }


def load_cache() -> None:
    # Note: All-time records are loaded on application start, while the session record starts from zero.
    global _LOADED
    with _LOCK:
        if _LOADED:
            return
        with connect() as conn:
            rows = conn.execute("SELECT * FROM transfer_speed_peaks").fetchall()
        for row in rows:
            profile_id = int(row.get("profile_id") or 0)
            if profile_id:
                _CACHE[profile_id] = _empty_peak(profile_id, row)
        _LOADED = True


def _ensure_profile(profile_id: int) -> dict[str, Any]:
    # Note: Lazy loading protects profiles added after startup from empty records.
    profile_id = int(profile_id)
    item = _CACHE.get(profile_id)
    if item:
        return item
    with connect() as conn:
        row = conn.execute("SELECT * FROM transfer_speed_peaks WHERE profile_id=?", (profile_id,)).fetchone()
    item = _empty_peak(profile_id, row)
    _CACHE[profile_id] = item
    return item


def _persist(item: dict[str, Any]) -> None:
    # Note: SQLite is updated only when a new session or all-time record appears.
    now = utcnow()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO transfer_speed_peaks(
              profile_id, session_started_at, session_down_peak, session_up_peak,
              session_down_peak_at, session_up_peak_at, all_time_down_peak,
              all_time_up_peak, all_time_down_peak_at, all_time_up_peak_at,
              created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(profile_id) DO UPDATE SET
              session_started_at=excluded.session_started_at,
              session_down_peak=excluded.session_down_peak,
              session_up_peak=excluded.session_up_peak,
              session_down_peak_at=excluded.session_down_peak_at,
              session_up_peak_at=excluded.session_up_peak_at,
              all_time_down_peak=excluded.all_time_down_peak,
              all_time_up_peak=excluded.all_time_up_peak,
              all_time_down_peak_at=excluded.all_time_down_peak_at,
              all_time_up_peak_at=excluded.all_time_up_peak_at,
              updated_at=excluded.updated_at
            """,
            (
                int(item["profile_id"]),
                item["session_started_at"],
                int(item["session_down_peak"]),
                int(item["session_up_peak"]),
                item.get("session_down_peak_at"),
                item.get("session_up_peak_at"),
                int(item["all_time_down_peak"]),
                int(item["all_time_up_peak"]),
                item.get("all_time_down_peak_at"),
                item.get("all_time_up_peak_at"),
                now,
                now,
            ),
        )


def _public(item: dict[str, Any]) -> dict[str, Any]:
    # Note: The frontend receives bytes/s and ready labels matching the existing speed format.
    return {
        "session_started_at": item["session_started_at"],
        "session": {
            "down": int(item["session_down_peak"]),
            "up": int(item["session_up_peak"]),
            "down_h": human_rate(int(item["session_down_peak"])),
            "up_h": human_rate(int(item["session_up_peak"])),
            "down_at": item.get("session_down_peak_at"),
            "up_at": item.get("session_up_peak_at"),
        },
        "all_time": {
            "down": int(item["all_time_down_peak"]),
            "up": int(item["all_time_up_peak"]),
            "down_h": human_rate(int(item["all_time_down_peak"])),
            "up_h": human_rate(int(item["all_time_up_peak"])),
            "down_at": item.get("all_time_down_peak_at"),
            "up_at": item.get("all_time_up_peak_at"),
        },
    }


def record(profile_id: int, down_rate: int = 0, up_rate: int = 0) -> dict[str, Any]:
    # Note: The poller calls this in the background; the database updates only after a record is beaten.
    load_cache()
    down_rate = max(0, int(down_rate or 0))
    up_rate = max(0, int(up_rate or 0))
    measured_at = utcnow()
    changed = False
    with _LOCK:
        item = _ensure_profile(int(profile_id))
        if down_rate > int(item["session_down_peak"]):
            item["session_down_peak"] = down_rate
            item["session_down_peak_at"] = measured_at
            changed = True
        if up_rate > int(item["session_up_peak"]):
            item["session_up_peak"] = up_rate
            item["session_up_peak_at"] = measured_at
            changed = True
        if down_rate > int(item["all_time_down_peak"]):
            item["all_time_down_peak"] = down_rate
            item["all_time_down_peak_at"] = measured_at
            changed = True
        if up_rate > int(item["all_time_up_peak"]):
            item["all_time_up_peak"] = up_rate
            item["all_time_up_peak_at"] = measured_at
            changed = True
        result = _public(item)
        if changed:
            _persist(item)
        return result


def current(profile_id: int) -> dict[str, Any]:
    # Note: The REST API can show the latest known record without forcing a new measurement.
    load_cache()
    with _LOCK:
        return _public(_ensure_profile(int(profile_id)))
