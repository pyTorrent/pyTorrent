from __future__ import annotations

import os
import threading
import time
from typing import Any
from ..db import connect, default_user_id
from . import port_check, preferences, rtorrent, tracker_cache
from .torrent_cache import torrent_cache

STARTUP_DELAY_SECONDS = 60
DEFAULT_TRACKER_INTERVAL_SECONDS = 15 * 60
DEFAULT_PORT_INTERVAL_SECONDS = port_check.PORT_CHECK_CACHE_SECONDS
FAVICON_BATCH_SIZE = 20

_started = False
_start_lock = threading.Lock()
_status_lock = threading.Lock()
_status: dict[str, Any] = {
    "started": False,
    "tracker_warmup": {},
    "port_check": {},
}


def _setting_float(name: str, default: float, minimum: float, maximum: float) -> float:
    """Read a bounded worker interval from the environment."""
    # Note: Defaults keep the worker light while still making UI-independent caches fresh after startup.
    try:
        value = float(os.environ.get(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def _profiles() -> list[dict[str, Any]]:
    """Read every rTorrent profile directly from the database."""
    # Note: The worker cannot rely on active browser session state, so it iterates real configured profiles.
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM rtorrent_profiles ORDER BY id").fetchall()]


def _owner_user_id(profile: dict[str, Any]) -> int:
    """Return the profile owner used for profile-scoped preferences."""
    return int(profile.get("user_id") or default_user_id())


def _connected(profile: dict[str, Any]) -> tuple[bool, str]:
    """Check rTorrent connectivity without changing user state."""
    try:
        rtorrent.client_for(profile).call("system.client_version")
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _remember(section: str, profile_id: int, payload: dict[str, Any]) -> None:
    """Store lightweight in-memory diagnostics for app/status."""
    # Note: Cache warmups are not user operations, so they stay out of operation logs by default.
    with _status_lock:
        data = dict(_status.get(section) or {})
        data[str(profile_id)] = {**payload, "updated_at_epoch": time.time()}
        _status[section] = data


def status() -> dict[str, Any]:
    """Return current worker diagnostics for system status endpoints."""
    with _status_lock:
        return {
            "started": bool(_status.get("started")),
            "startup_delay_seconds": STARTUP_DELAY_SECONDS,
            "tracker_warmup": dict(_status.get("tracker_warmup") or {}),
            "port_check": dict(_status.get("port_check") or {}),
        }


def _tracker_domains_from_rows(rows: list[dict[str, Any]], summary: dict[str, Any], profile_id: int) -> list[str]:
    """Build a bounded tracker domain list from fresh summary data and cached rows."""
    domains = [str(item.get("domain") or "") for item in summary.get("trackers") or []]
    if not domains:
        domains = tracker_cache.cached_domains_for_profile(profile_id, limit=200)
    return domains


def _warm_tracker_profile(profile: dict[str, Any]) -> None:
    """Warm tracker summary cache and optional favicon cache for one profile."""
    # Note: This mirrors the sidebar warmup, but runs from the backend scheduler instead of waiting for the filter panel.
    profile_id = int(profile.get("id") or 0)
    if not profile_id:
        return
    ok, error = _connected(profile)
    if not ok:
        _remember("tracker_warmup", profile_id, {"ok": False, "skipped": True, "reason": "rtorrent_disconnected", "error": error})
        return

    owner_id = _owner_user_id(profile)
    prefs = preferences.get_preferences(owner_id, profile_id)
    rows = torrent_cache.snapshot(profile_id)
    if not rows:
        torrent_cache.refresh(profile)
        rows = torrent_cache.snapshot(profile_id)
    hashes = [str(row.get("hash") or "") for row in rows if row.get("hash")]
    if not hashes:
        _remember("tracker_warmup", profile_id, {"ok": True, "skipped": True, "reason": "no_torrents"})
        return

    loader = lambda h: rtorrent.torrent_trackers(profile, h)
    summary = tracker_cache.summary(profile, hashes, loader, scan_limit=tracker_cache.TRACKER_SCAN_LIMIT, include_favicons=False)
    warming = False
    if int(summary.get("pending") or 0) > 0:
        warming = tracker_cache.warm_summary_cache(profile, hashes, loader, batch_size=tracker_cache.TRACKER_SCAN_LIMIT)

    favicon_result = {"checked": 0, "cached": 0, "errors": []}
    if bool((prefs or {}).get("tracker_favicons_enabled")):
        domains = _tracker_domains_from_rows(rows, summary, profile_id)
        favicon_result = tracker_cache.warm_favicon_cache(domains, enabled=True, limit=FAVICON_BATCH_SIZE, force=False)

    _remember(
        "tracker_warmup",
        profile_id,
        {
            "ok": True,
            "hashes": len(hashes),
            "pending": int(summary.get("pending") or 0),
            "scanned_now": int(summary.get("scanned_now") or 0),
            "warming": bool(warming),
            "favicons_enabled": bool((prefs or {}).get("tracker_favicons_enabled")),
            "favicons": favicon_result,
        },
    )


def _check_port_profile(profile: dict[str, Any]) -> None:
    """Refresh incoming-port status when the profile preference enables it."""
    # Note: force=False respects the existing six-hour cache and avoids unnecessary external checks.
    profile_id = int(profile.get("id") or 0)
    if not profile_id:
        return
    owner_id = _owner_user_id(profile)
    prefs = preferences.get_preferences(owner_id, profile_id)
    if not bool((prefs or {}).get("port_check_enabled")):
        _remember("port_check", profile_id, {"ok": True, "enabled": False, "skipped": True, "reason": "disabled"})
        return
    result = port_check.port_check_status(profile=profile, force=False, user_id=owner_id)
    _remember(
        "port_check",
        profile_id,
        {
            "ok": not bool(result.get("error") and result.get("source") == "none"),
            "enabled": True,
            "status": result.get("status"),
            "cached": bool(result.get("cached")),
            "checked_at": result.get("checked_at"),
            "error": result.get("error") or result.get("fallback_error") or "",
        },
    )


def start_scheduler(socketio=None) -> None:
    """Start browser-independent cache warmup and port-check scheduler."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
        with _status_lock:
            _status["started"] = True

    tracker_interval = _setting_float("PYTORRENT_CACHE_WARMUP_INTERVAL_SECONDS", DEFAULT_TRACKER_INTERVAL_SECONDS, 60.0, 24 * 60 * 60.0)
    port_interval = _setting_float("PYTORRENT_PORT_CHECK_INTERVAL_SECONDS", DEFAULT_PORT_INTERVAL_SECONDS, 60.0, 24 * 60 * 60.0)

    def runner() -> None:
        time.sleep(STARTUP_DELAY_SECONDS)
        last_tracker: dict[int, float] = {}
        last_port: dict[int, float] = {}
        while True:
            now = time.monotonic()
            next_sleep = 60.0
            for profile in _profiles():
                profile_id = int(profile.get("id") or 0)
                if not profile_id:
                    continue
                if now - float(last_tracker.get(profile_id) or 0.0) >= tracker_interval:
                    last_tracker[profile_id] = now
                    try:
                        _warm_tracker_profile(profile)
                    except Exception as exc:
                        _remember("tracker_warmup", profile_id, {"ok": False, "error": str(exc)})
                if now - float(last_port.get(profile_id) or 0.0) >= port_interval:
                    last_port[profile_id] = now
                    try:
                        _check_port_profile(profile)
                    except Exception as exc:
                        _remember("port_check", profile_id, {"ok": False, "error": str(exc)})
                next_sleep = min(
                    next_sleep,
                    max(1.0, tracker_interval - (time.monotonic() - float(last_tracker.get(profile_id) or 0.0))),
                    max(1.0, port_interval - (time.monotonic() - float(last_port.get(profile_id) or 0.0))),
                )
            sleep_for = max(5.0, min(60.0, next_sleep))
            if socketio:
                socketio.sleep(sleep_for)
            else:
                time.sleep(sleep_for)

    if socketio:
        socketio.start_background_task(runner)
    else:
        threading.Thread(target=runner, daemon=True, name="pytorrent-cache-warmup-scheduler").start()
