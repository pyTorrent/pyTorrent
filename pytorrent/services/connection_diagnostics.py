from __future__ import annotations

import threading
import time
from typing import Any

from flask import request

from . import auth, poller_control, rtorrent

_LOCK = threading.RLock()
_PROFILE_STATE: dict[int, dict[str, Any]] = {}


def _state(profile_id: int) -> dict[str, Any]:
    profile_id = int(profile_id)
    with _LOCK:
        return _PROFILE_STATE.setdefault(
            profile_id,
            {
                "profile_id": profile_id,
                "last_attempt_at_epoch": 0.0,
                "last_success_at_epoch": 0.0,
                "last_failure_at_epoch": 0.0,
                "last_duration_ms": 0.0,
                "last_source": "",
                "last_ok": None,
                "last_error": "",
                "sources": {},
                "last_probe": {},
            },
        )


def record_scgi_activity(
    profile_id: int,
    source: str,
    ok: bool,
    duration_ms: float = 0.0,
    error: str = "",
    **numeric_details: Any,
) -> None:
    """Record facts from an SCGI call that the normal application flow already performed."""
    now = time.time()
    clean_details = {
        str(key): value
        for key, value in numeric_details.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    with _LOCK:
        item = _state(profile_id)
        item.update(
            {
                "last_attempt_at_epoch": now,
                "last_duration_ms": round(max(0.0, float(duration_ms or 0.0)), 2),
                "last_source": str(source or "unknown"),
                "last_ok": bool(ok),
                "last_error": "" if ok else str(error or "Unknown SCGI error"),
            }
        )
        if ok:
            item["last_success_at_epoch"] = now
        else:
            item["last_failure_at_epoch"] = now
        item["sources"][str(source or "unknown")] = {
            "ok": bool(ok),
            "duration_ms": item["last_duration_ms"],
            "error": "" if ok else str(error or "Unknown SCGI error"),
            "at_epoch": now,
            **clean_details,
        }


def _age_seconds(epoch: float) -> float | None:
    if not epoch:
        return None
    return round(max(0.0, time.time() - float(epoch)), 1)


def passive_snapshot(profile: dict) -> dict[str, Any]:
    """Return connection diagnostics without causing any network request."""
    profile_id = int(profile.get("id") or 0)
    runtime = poller_control.snapshot(profile_id)
    with _LOCK:
        observed = dict(_state(profile_id))
        observed["sources"] = {key: dict(value) for key, value in (observed.get("sources") or {}).items()}
        observed["last_probe"] = dict(observed.get("last_probe") or {})
    observed["last_success_age_seconds"] = _age_seconds(float(observed.get("last_success_at_epoch") or 0.0))
    observed["last_attempt_age_seconds"] = _age_seconds(float(observed.get("last_attempt_at_epoch") or 0.0))
    observed["last_failure_age_seconds"] = _age_seconds(float(observed.get("last_failure_at_epoch") or 0.0))
    return {
        "profile": {
            "id": profile_id,
            "name": str(profile.get("name") or f"rTorrent {profile_id}"),
            "scgi_url": str(profile.get("scgi_url") or ""),
            "remote": bool(profile.get("is_remote")),
        },
        "scgi": observed,
        "poller": runtime,
    }


def metrics_snapshot(profile_id: int) -> dict[str, Any]:
    """Return the in-memory SCGI observation state for Prometheus without polling anything."""
    with _LOCK:
        observed = dict(_state(profile_id))
        observed["sources"] = {key: dict(value) for key, value in (observed.get("sources") or {}).items()}
        observed["last_probe"] = dict(observed.get("last_probe") or {})
    observed["last_success_age_seconds"] = _age_seconds(float(observed.get("last_success_at_epoch") or 0.0))
    observed["last_attempt_age_seconds"] = _age_seconds(float(observed.get("last_attempt_at_epoch") or 0.0))
    return observed


def run_active_scgi_probe(profile: dict) -> dict[str, Any]:
    """Run the explicit SCGI probe requested by the user from the diagnostics popover."""
    profile_id = int(profile.get("id") or 0)
    started = time.perf_counter()
    try:
        result = rtorrent.scgi_diagnostics(profile)
        record_scgi_activity(
            profile_id,
            "probe",
            True,
            float(result.get("total_ms") or 0.0),
            request_bytes=result.get("request_bytes"),
            response_bytes=result.get("response_bytes"),
            connect_ms=result.get("connect_ms"),
            first_byte_ms=result.get("first_byte_ms"),
            total_ms=result.get("total_ms"),
        )
        probe = dict(result)
    except Exception as exc:
        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
        record_scgi_activity(profile_id, "probe", False, duration_ms, str(exc))
        probe = {
            "ok": False,
            "url": str(profile.get("scgi_url") or ""),
            "total_ms": duration_ms,
            "error": str(exc),
        }
    probe["measured_at_epoch"] = time.time()
    with _LOCK:
        _state(profile_id)["last_probe"] = dict(probe)
    return probe


def register_socketio_handlers(socketio) -> None:
    @socketio.on("connection_diagnostics_ping")
    def connection_diagnostics_ping(data=None):
        # Note: The handler only acknowledges the existing Socket.IO connection; it never touches rTorrent.
        if auth.enabled() and not auth.ensure_request_user():
            return {"ok": False, "error": "Authentication required"}
        try:
            transport = str(socketio.server.transport(str(request.sid), namespace="/") or "unknown")
        except Exception:
            transport = "unknown"
        return {
            "ok": True,
            "profile_id": int((data or {}).get("profile_id") or 0),
            "transport": transport,
            "server_time_ms": round(time.time() * 1000.0, 3),
        }
