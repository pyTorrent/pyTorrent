from __future__ import annotations

import json
import threading
import time
from typing import Any

from ..db import connect, utcnow

_CACHE: dict[int, dict[str, Any]] = {}
_CACHE_LOCK = threading.RLock()
_LAST_DB_WRITE: dict[int, float] = {}
_DB_WRITE_INTERVAL_SECONDS = 15.0


def _profile_id(profile_or_id: dict | int | None) -> int:
    # Note: Normalize service callers to the same integer key so cache entries can never overlap between profiles.
    if isinstance(profile_or_id, dict):
        return int(profile_or_id.get("id") or 0)
    return int(profile_or_id or 0)


def _clone_status(status: dict[str, Any] | None) -> dict[str, Any] | None:
    # Note: Return detached snapshots so route/UI enrichment cannot mutate the shared in-memory cache.
    if not isinstance(status, dict):
        return None
    try:
        return json.loads(json.dumps(status, default=str))
    except (TypeError, ValueError):
        return dict(status)


def get_status(profile_or_id: dict | int | None) -> dict[str, Any] | None:
    """Return the last application-owned system snapshot for one profile."""
    # Note: RAM is the fast path while SQLite keeps the last small footer/disk snapshot available after process restarts.
    profile_id = _profile_id(profile_or_id)
    if not profile_id:
        return None
    with _CACHE_LOCK:
        cached = _CACHE.get(profile_id)
        if cached is not None:
            return _clone_status(cached)
    with connect() as conn:
        row = conn.execute(
            "SELECT status_json, updated_at FROM profile_status_cache WHERE profile_id=?",
            (profile_id,),
        ).fetchone()
    if not row:
        return None
    try:
        status = json.loads(row.get("status_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(status, dict):
        return None
    status["profile_id"] = profile_id
    status.setdefault("footer_updated_at", row.get("updated_at"))
    with _CACHE_LOCK:
        _CACHE[profile_id] = status
    return _clone_status(status)


def store_status(profile_or_id: dict | int | None, status: dict[str, Any] | None, *, persist: bool = True) -> dict[str, Any] | None:
    """Store one complete profile system snapshot in RAM and periodically in SQLite."""
    # Note: Frequent poller updates always refresh RAM; SQLite writes are throttled because the persisted snapshot is only restart fallback state.
    profile_id = _profile_id(profile_or_id)
    clean = _clone_status(status)
    if not profile_id or clean is None:
        return None
    updated_at = utcnow()
    clean["profile_id"] = profile_id
    clean["footer_updated_at"] = updated_at
    with _CACHE_LOCK:
        _CACHE[profile_id] = clean
        now = time.monotonic()
        should_persist = persist and (now - float(_LAST_DB_WRITE.get(profile_id) or 0.0) >= _DB_WRITE_INTERVAL_SECONDS)
        if should_persist:
            _LAST_DB_WRITE[profile_id] = now
    if should_persist:
        payload = json.dumps(clean, separators=(",", ":"), default=str)
        try:
            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO profile_status_cache(profile_id,status_json,updated_at)
                    VALUES(?,?,?)
                    ON CONFLICT(profile_id) DO UPDATE SET status_json=excluded.status_json, updated_at=excluded.updated_at
                    """,
                    (profile_id, payload, updated_at),
                )
        except Exception:
            # RAM remains authoritative for the current process when a transient SQLite write fails.
            with _CACHE_LOCK:
                _LAST_DB_WRITE.pop(profile_id, None)
    return _clone_status(clean)


def store_disk(profile_or_id: dict | int | None, disk: dict[str, Any] | None) -> dict[str, Any] | None:
    """Merge a fresh disk reading into the cached profile snapshot."""
    # Note: Disk-only refreshes preserve cached rTorrent footer metadata instead of rebuilding it synchronously.
    profile_id = _profile_id(profile_or_id)
    if not profile_id or not isinstance(disk, dict):
        return None
    cached = get_status(profile_id) or {"profile_id": profile_id}
    cached["disk"] = _clone_status(disk) or {}
    return store_status(profile_id, cached)


def profile_disk_status(profile: dict) -> dict[str, Any]:
    """Read the configured disk-monitor source for one rTorrent profile."""
    # Note: This central helper keeps route and background-poller disk snapshots identical and pins legacy fallback settings to the profile owner.
    from . import preferences, rtorrent

    profile_id = _profile_id(profile)
    profile_owner_id = int(profile.get("user_id") or 0) or None
    prefs = preferences.get_disk_monitor_preferences(profile_id, user_id=profile_owner_id)
    try:
        paths = json.loads((prefs or {}).get("disk_monitor_paths_json") or "[]") if prefs else []
    except (TypeError, ValueError, json.JSONDecodeError):
        paths = []
    return rtorrent.disk_usage_for_paths(
        profile,
        paths,
        (prefs or {}).get("disk_monitor_mode") or "default",
        (prefs or {}).get("disk_monitor_selected_path") or "",
    )


def clear_profile(profile_or_id: dict | int | None) -> int:
    """Clear persisted and in-memory system status cache for one profile."""
    # Note: Maintenance cleanup removes both cache tiers for exactly one profile.
    profile_id = _profile_id(profile_or_id)
    if not profile_id:
        return 0
    with _CACHE_LOCK:
        removed = 1 if _CACHE.pop(profile_id, None) is not None else 0
        _LAST_DB_WRITE.pop(profile_id, None)
    with connect() as conn:
        deleted = int(conn.execute("DELETE FROM profile_status_cache WHERE profile_id=?", (profile_id,)).rowcount or 0)
    return max(removed, deleted)
