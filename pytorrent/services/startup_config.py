from __future__ import annotations
import threading
from time import monotonic
from ..db import connect
from . import operation_logs, rtorrent

_started = False
_start_lock = threading.Lock()
_applied_profiles: set[int] = set()
_last_status: dict[int, str] = {}


def _profiles() -> list[dict]:
    """Read all configured profiles because startup work has no browser user session."""
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM rtorrent_profiles ORDER BY id").fetchall()]


def _log_status(profile: dict, status: str, message: str, *, error: str = "", result: dict | None = None) -> None:
    """Write meaningful startup config state changes as system operations."""
    profile_id = int(profile.get("id") or 0)
    if status in {"waiting", "skipped"} and _last_status.get(profile_id) == status:
        return
    _last_status[profile_id] = status
    operation_logs.record(
        profile_id,
        "rtorrent_config_startup",
        message,
        severity="warning" if error else "info",
        source="system",
        action="rtorrent_config",
        details={"status": status, "error": error, "result": result or {}},
        user_id=int(profile.get("user_id") or 0) or None,
    )


def _rtorrent_ready(profile: dict) -> tuple[bool, str]:
    """Check rTorrent before applying saved runtime overrides."""
    try:
        rtorrent.client_for(profile).call("system.client_version")
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _apply_profile(socketio, profile: dict) -> None:
    """Apply saved config only after the target rTorrent is reachable."""
    profile_id = int(profile.get("id") or 0)
    if not profile_id or profile_id in _applied_profiles:
        return
    ok, error = _rtorrent_ready(profile)
    if not ok:
        _log_status(profile, "waiting", f"rTorrent config apply is waiting for connection: {error}", error=error)
        return
    result = rtorrent.apply_startup_overrides(profile)
    if result.get("skipped"):
        _applied_profiles.add(profile_id)
        _log_status(profile, "skipped", "No saved rTorrent startup config overrides to apply", result=result)
        return
    _applied_profiles.add(profile_id)
    _log_status(profile, "applied", "Saved rTorrent startup config overrides applied", result=result)
    socketio.emit("rtorrent_config_applied", {"profile_id": profile_id, "result": result}, to=f"profile:{int(profile_id)}")


def schedule_startup_config_apply(socketio, delay_seconds: int = 60, retry_seconds: int = 30, max_wait_seconds: int = 3600) -> None:
    """Apply saved rTorrent UI overrides after the configured startup delay without requiring a browser."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True

    def runner() -> None:
        socketio.sleep(max(0, int(delay_seconds)))
        started_at = monotonic()
        while True:
            failed_profile_id = 0
            try:
                profiles = _profiles()
                for profile in profiles:
                    failed_profile_id = int(profile.get("id") or 0)
                    # Note: Startup config applies per profile after connectivity is detected; it does not depend on the active UI profile.
                    _apply_profile(socketio, profile)
                pending = [int(profile.get("id") or 0) for profile in profiles if int(profile.get("id") or 0) not in _applied_profiles]
                if not pending or monotonic() - started_at >= max(0, int(max_wait_seconds)):
                    for profile in profiles:
                        profile_id = int(profile.get("id") or 0)
                        if profile_id in pending:
                            _log_status(profile, "timeout", "rTorrent config startup apply stopped waiting for connection", error="startup wait timeout")
                    return
            except Exception as exc:
                operation_logs.record(
                    failed_profile_id or None,
                    "rtorrent_config_startup",
                    f"rTorrent startup config scheduler failed: {exc}",
                    severity="warning",
                    source="system",
                    action="rtorrent_config",
                    details={"error": str(exc)},
                )
                socketio.emit("rtorrent_config_applied", {"ok": False, "profile_id": int(failed_profile_id or 0), "error": str(exc)}, to=f"profile:{int(failed_profile_id)}" if failed_profile_id else None)
            socketio.sleep(max(5, int(retry_seconds)))

    socketio.start_background_task(runner)
