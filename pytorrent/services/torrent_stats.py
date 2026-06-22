from __future__ import annotations
import json
import threading
import time
from typing import Any
from ..db import connect, utcnow
from . import rtorrent
from .torrent_cache import torrent_cache

CACHE_SECONDS = 15 * 60
_STARTUP_DELAY_SECONDS = 3 * 60
_STARTED_AT = time.monotonic()
_LOCK = threading.Lock()
_BACKGROUND_LOCK = threading.Lock()
_BACKGROUND_PROFILE_IDS: set[int] = set()


def _human_size(value: int | float) -> str:
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(size) < 1024 or unit == "PiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} PiB"


def _empty(profile_id: int, error: str = "") -> dict[str, Any]:
    now = utcnow()
    return {
        "profile_id": profile_id,
        "torrent_count": 0,
        "complete_count": 0,
        "incomplete_count": 0,
        "total_torrent_size": 0,
        "total_torrent_size_h": _human_size(0),
        "total_file_size": 0,
        "total_file_size_h": _human_size(0),
        "file_count": 0,
        "seeds_total": 0,
        "peers_total": 0,
        "down_rate_total": 0,
        "up_rate_total": 0,
        "down_rate_total_h": "0 B/s",
        "up_rate_total_h": "0 B/s",
        "sampled_torrents": 0,
        "errors": [],
        "error": error,
        "created_at": now,
        "updated_at": now,
        "age_seconds": 0,
        "stale": True,
    }


def _load_cached(profile_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM torrent_stats_cache WHERE profile_id=?", (profile_id,)).fetchone()
    if not row:
        return None
    payload = json.loads(row.get("payload_json") or "{}")
    payload["created_at"] = row.get("created_at")
    payload["updated_at"] = row.get("updated_at")
    try:
        payload["age_seconds"] = max(0, int(time.time() - float(row.get("updated_epoch") or 0)))
    except Exception:
        payload["age_seconds"] = 0
    payload["stale"] = payload["age_seconds"] >= CACHE_SECONDS
    return payload


def _save(profile_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    now = utcnow()
    payload = dict(payload)
    payload["updated_at"] = now
    payload["age_seconds"] = 0
    payload["stale"] = False
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO torrent_stats_cache(profile_id,payload_json,created_at,updated_at,updated_epoch)
            VALUES(?,?,?,?,?)
            ON CONFLICT(profile_id) DO UPDATE SET
              payload_json=excluded.payload_json,
              updated_at=excluded.updated_at,
              updated_epoch=excluded.updated_epoch
            """,
            (profile_id, json.dumps(payload), now, now, time.time()),
        )
    return payload


def collect(profile: dict) -> dict[str, Any]:
    """Collect heavier torrent/file statistics on demand or every cache window."""
    profile_id = int(profile.get("id") or 0)
    torrents = rtorrent.list_torrents(profile)
    total_torrent_size = sum(int(t.get("size") or 0) for t in torrents)
    seeds_total = sum(int(t.get("seeds") or 0) for t in torrents)
    peers_total = sum(int(t.get("peers") or 0) for t in torrents)
    down_rate_total = sum(int(t.get("down_rate") or 0) for t in torrents)
    up_rate_total = sum(int(t.get("up_rate") or 0) for t in torrents)
    total_file_size = 0
    file_count = 0
    errors: list[dict[str, str]] = []

    # Note: File metadata is queried per torrent only during cached statistics refresh, not during every UI poll.
    for torrent in torrents:
        h = str(torrent.get("hash") or "")
        if not h:
            continue
        try:
            files = rtorrent.torrent_files(profile, h)
            file_count += len(files)
            total_file_size += sum(int(f.get("size") or 0) for f in files)
        except Exception as exc:
            errors.append({"hash": h, "name": str(torrent.get("name") or ""), "error": str(exc)})

    torrent_cache.refresh(profile)
    payload = {
        "profile_id": profile_id,
        "torrent_count": len(torrents),
        "complete_count": sum(1 for t in torrents if int(t.get("complete") or 0)),
        "incomplete_count": sum(1 for t in torrents if not int(t.get("complete") or 0)),
        "total_torrent_size": total_torrent_size,
        "total_torrent_size_h": _human_size(total_torrent_size),
        "total_file_size": total_file_size,
        "total_file_size_h": _human_size(total_file_size),
        "file_count": file_count,
        "seeds_total": seeds_total,
        "peers_total": peers_total,
        "down_rate_total": down_rate_total,
        "up_rate_total": up_rate_total,
        "down_rate_total_h": rtorrent.human_rate(down_rate_total),
        "up_rate_total_h": rtorrent.human_rate(up_rate_total),
        "sampled_torrents": len(torrents),
        "errors": errors[:25],
        "error": "" if not errors else f"File metadata failed for {len(errors)} torrent(s)",
        "created_at": utcnow(),
    }
    return _save(profile_id, payload)


def get(profile: dict | None, force: bool = False) -> dict[str, Any]:
    if not profile:
        return _empty(0, "No active rTorrent profile")
    profile_id = int(profile.get("id") or 0)
    cached = _load_cached(profile_id)
    if cached and not force and not cached.get("stale"):
        return cached
    if cached and not force:
        return cached
    with _LOCK:
        cached = _load_cached(profile_id)
        if cached and not force and not cached.get("stale"):
            return cached
        return collect(profile)


def maybe_refresh(profile: dict | None, force: bool = False) -> dict[str, Any] | None:
    if not profile:
        return None
    if not force and time.monotonic() - _STARTED_AT < _STARTUP_DELAY_SECONDS:
        return None
    cached = _load_cached(int(profile.get("id") or 0))
    if cached and not cached.get("stale") and not force:
        return cached
    try:
        return get(profile, force=True)
    except Exception:
        return cached


def queue_refresh(socketio, profile: dict | None, force: bool = False, emit_update: bool = True, room: str | None = None) -> dict[str, Any] | None:
    """Schedule heavier statistics refresh outside the main WebSocket/system poller."""
    if not profile:
        return None
    if not force and time.monotonic() - _STARTED_AT < _STARTUP_DELAY_SECONDS:
        return _load_cached(int(profile.get("id") or 0))

    profile_id = int(profile.get("id") or 0)
    cached = _load_cached(profile_id)
    if cached and not cached.get("stale") and not force:
        return cached

    with _BACKGROUND_LOCK:
        if profile_id in _BACKGROUND_PROFILE_IDS:
            return cached
        _BACKGROUND_PROFILE_IDS.add(profile_id)

    profile_snapshot = dict(profile)

    def runner():
        try:
            # Note: This can query file metadata per torrent, so it never runs inside the fast CPU/RAM/disk poller.
            stats = get(profile_snapshot, force=True)
            if emit_update and stats:
                payload = {"profile_id": profile_id, "stats": stats}
                socketio.emit("torrent_stats_update", payload, to=room) if room else socketio.emit("torrent_stats_update", payload)
        except Exception as exc:
            if emit_update:
                payload = {"profile_id": profile_id, "ok": False, "error": str(exc)}
                socketio.emit("torrent_stats_update", payload, to=room) if room else socketio.emit("torrent_stats_update", payload)
        finally:
            with _BACKGROUND_LOCK:
                _BACKGROUND_PROFILE_IDS.discard(profile_id)

    socketio.start_background_task(runner)
    return cached
