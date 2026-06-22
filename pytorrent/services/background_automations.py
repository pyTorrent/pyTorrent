from __future__ import annotations

import os
import threading
import time
from typing import Any
from ..db import connect, default_user_id
from . import automation_rules, operation_logs, poller_control, rtorrent
from .websocket import emit_profile_event

_started = False
_start_lock = threading.Lock()
_profile_locks: dict[int, threading.Lock] = {}
_profile_locks_lock = threading.Lock()
_last_logged_status: dict[int, str] = {}


def _configured_interval() -> float:
    """Return the minimum background automation interval from environment settings."""
    try:
        return max(5.0, min(3600.0, float(os.environ.get("PYTORRENT_AUTOMATION_BACKGROUND_INTERVAL_SECONDS", "15"))))
    except Exception:
        return 15.0


def _profiles() -> list[dict[str, Any]]:
    """Read configured profiles without relying on a browser session."""
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM rtorrent_profiles ORDER BY id").fetchall()]


def _profile_lock(profile_id: int) -> threading.Lock:
    """Keep one automation pass per profile active at a time."""
    with _profile_locks_lock:
        if profile_id not in _profile_locks:
            _profile_locks[profile_id] = threading.Lock()
        return _profile_locks[profile_id]


def _owner_user_id(profile: dict[str, Any]) -> int:
    """Use the profile owner for background checks so rule permissions stay stable."""
    return int(profile.get("user_id") or default_user_id())


def _profile_interval(profile_id: int) -> float:
    """Reuse the existing queue poller cadence instead of adding another UI setting."""
    settings = poller_control.get_settings(profile_id)
    return max(_configured_interval(), float(settings.get("queue_stats_interval_seconds") or 15.0))


def _connected(profile: dict[str, Any]) -> tuple[bool, str]:
    """Verify rTorrent connectivity before running automation logic."""
    try:
        rtorrent.client_for(profile).call("system.client_version")
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _log_status(profile_id: int, status: str, message: str, *, error: str = "") -> None:
    """Log only connectivity state changes to avoid noisy system logs."""
    if _last_logged_status.get(profile_id) == status:
        return
    _last_logged_status[profile_id] = status
    severity = "warning" if error else "info"
    operation_logs.record(
        profile_id,
        "background_automation_status",
        message,
        severity=severity,
        source="system",
        action="background_automation",
        details={"status": status, "error": error},
    )


def _run_profile(socketio, profile: dict[str, Any]) -> None:
    """Run one safe background automation pass for a connected profile."""
    profile_id = int(profile.get("id") or 0)
    if not profile_id:
        return
    lock = _profile_lock(profile_id)
    if not lock.acquire(blocking=False):
        return
    try:
        ok, error = _connected(profile)
        if not ok:
            _log_status(profile_id, "disconnected", f"Background automations waiting for rTorrent: {error}", error=error)
            return
        _log_status(profile_id, "connected", "Background automations detected a working rTorrent connection")
        result = automation_rules.check(profile, user_id=_owner_user_id(profile), force=False)
        if result.get("applied") or result.get("batches"):
            operation_logs.record(
                profile_id,
                "background_automation_run",
                "Background automations applied matching rules",
                source="system",
                action="background_automation",
                details={"applied": len(result.get("applied") or []), "batches": len(result.get("batches") or []), "result": result},
                user_id=_owner_user_id(profile),
            )
            emit_profile_event(socketio, "automation_update", result, profile_id)
    except Exception as exc:
        operation_logs.record(
            profile_id,
            "background_automation_error",
            f"Background automation check failed: {exc}",
            severity="warning",
            source="system",
            action="background_automation",
            details={"error": str(exc)},
            user_id=_owner_user_id(profile),
        )
    finally:
        lock.release()


def start_scheduler(socketio) -> None:
    """Start browser-independent automation checks once per application process."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True

    def runner() -> None:
        last_run: dict[int, float] = {}
        while True:
            started = time.monotonic()
            next_sleep = _configured_interval()
            for profile in _profiles():
                profile_id = int(profile.get("id") or 0)
                if not profile_id:
                    continue
                interval = _profile_interval(profile_id)
                elapsed = started - float(last_run.get(profile_id) or 0.0)
                if elapsed < interval:
                    next_sleep = min(next_sleep, max(1.0, interval - elapsed))
                    continue
                last_run[profile_id] = started
                _run_profile(socketio, profile)
                next_sleep = min(next_sleep, interval)
            socketio.sleep(max(1.0, next_sleep))

    socketio.start_background_task(runner)
