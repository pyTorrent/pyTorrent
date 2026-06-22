from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from typing import Any
from ..db import connect, utcnow
from ..config import POLL_INTERVAL, MIN_POLL_INTERVAL_SECONDS

DEFAULTS = {
    "adaptive_enabled": True,
    "safe_fallback_enabled": True,
    "active_interval_seconds": 3.0,
    "idle_interval_seconds": 15.0,
    "error_interval_seconds": 30.0,
    "live_stats_interval_seconds": 3.0,
    "torrent_list_interval_seconds": 30.0,
    "system_stats_interval_seconds": 5.0,
    "tracker_stats_interval_seconds": 300.0,
    "disk_stats_interval_seconds": 60.0,
    "queue_stats_interval_seconds": 15.0,
    "slow_stats_interval_seconds": 60.0,
    "heartbeat_interval_seconds": 15.0,
    "emit_heartbeat_on_change": True,
    "slow_response_threshold_ms": 8000.0,
    "slowdown_multiplier": 2.0,
    "recovery_after_errors": 3,
}

SAFE_FALLBACK_MINIMUMS = {
    "active_interval_seconds": 3.0,
    "idle_interval_seconds": 15.0,
    "error_interval_seconds": 30.0,
    "live_stats_interval_seconds": 3.0,
    "torrent_list_interval_seconds": 30.0,
    "system_stats_interval_seconds": 5.0,
    "tracker_stats_interval_seconds": 300.0,
    "disk_stats_interval_seconds": 60.0,
    "queue_stats_interval_seconds": 15.0,
    "slow_stats_interval_seconds": 60.0,
    "heartbeat_interval_seconds": 15.0,
}


def _key(profile_id: int) -> str:
    return f"poller.settings.{int(profile_id)}"


def _state_key(profile_id: int) -> str:
    return f"poller.runtime.{int(profile_id)}"


def _coerce_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except Exception:
        return default
    return max(lo, min(hi, number))


def normalize_settings(data: dict | None) -> dict:
    raw = {**DEFAULTS, **(data or {})}
    settings = {
        "adaptive_enabled": bool(raw.get("adaptive_enabled")),
        "safe_fallback_enabled": bool(raw.get("safe_fallback_enabled", True)),
        "active_interval_seconds": _coerce_float(raw.get("active_interval_seconds"), DEFAULTS["active_interval_seconds"], MIN_POLL_INTERVAL_SECONDS, 30.0),
        "idle_interval_seconds": _coerce_float(raw.get("idle_interval_seconds"), DEFAULTS["idle_interval_seconds"], MIN_POLL_INTERVAL_SECONDS, 120.0),
        "error_interval_seconds": _coerce_float(raw.get("error_interval_seconds"), DEFAULTS["error_interval_seconds"], MIN_POLL_INTERVAL_SECONDS, 300.0),
        "live_stats_interval_seconds": _coerce_float(raw.get("live_stats_interval_seconds"), DEFAULTS["live_stats_interval_seconds"], MIN_POLL_INTERVAL_SECONDS, 60.0),
        "torrent_list_interval_seconds": _coerce_float(raw.get("torrent_list_interval_seconds"), DEFAULTS["torrent_list_interval_seconds"], MIN_POLL_INTERVAL_SECONDS, 120.0),
        "system_stats_interval_seconds": _coerce_float(raw.get("system_stats_interval_seconds"), DEFAULTS["system_stats_interval_seconds"], MIN_POLL_INTERVAL_SECONDS, 120.0),
        "tracker_stats_interval_seconds": _coerce_float(raw.get("tracker_stats_interval_seconds"), DEFAULTS["tracker_stats_interval_seconds"], MIN_POLL_INTERVAL_SECONDS, 1800.0),
        "disk_stats_interval_seconds": _coerce_float(raw.get("disk_stats_interval_seconds"), DEFAULTS["disk_stats_interval_seconds"], MIN_POLL_INTERVAL_SECONDS, 1800.0),
        "queue_stats_interval_seconds": _coerce_float(raw.get("queue_stats_interval_seconds"), DEFAULTS["queue_stats_interval_seconds"], MIN_POLL_INTERVAL_SECONDS, 1800.0),
        "slow_stats_interval_seconds": _coerce_float(raw.get("slow_stats_interval_seconds"), DEFAULTS["slow_stats_interval_seconds"], MIN_POLL_INTERVAL_SECONDS, 1800.0),
        "heartbeat_interval_seconds": _coerce_float(raw.get("heartbeat_interval_seconds"), DEFAULTS["heartbeat_interval_seconds"], MIN_POLL_INTERVAL_SECONDS, 300.0),
        "emit_heartbeat_on_change": bool(raw.get("emit_heartbeat_on_change")),
        "slow_response_threshold_ms": _coerce_float(raw.get("slow_response_threshold_ms"), DEFAULTS["slow_response_threshold_ms"], 100.0, 60000.0),
        "slowdown_multiplier": _coerce_float(raw.get("slowdown_multiplier"), DEFAULTS["slowdown_multiplier"], 1.0, 10.0),
        "recovery_after_errors": int(_coerce_float(raw.get("recovery_after_errors"), 3, 1, 20)),
    }
    if settings["safe_fallback_enabled"]:
        for key, minimum in SAFE_FALLBACK_MINIMUMS.items():
            settings[key] = max(float(settings.get(key) or DEFAULTS[key]), float(minimum))
    return settings


def get_settings(profile_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT settings_json FROM poller_settings WHERE profile_id=?", (int(profile_id),)).fetchone()
        if not row:
            legacy = conn.execute("SELECT value FROM app_settings WHERE key=?", (_key(profile_id),)).fetchone()
            if legacy:
                try:
                    settings = normalize_settings(json.loads(legacy.get("value") or "{}"))
                except Exception:
                    settings = normalize_settings({})
                conn.execute("INSERT OR REPLACE INTO poller_settings(profile_id,settings_json,updated_at) VALUES(?,?,?)", (int(profile_id), json.dumps(settings), utcnow()))
                return settings
    try:
        data = json.loads(row.get("settings_json") or "{}") if row else {}
    except Exception:
        data = {}
    return normalize_settings(data)


def save_settings(profile_id: int, data: dict) -> dict:
    settings = normalize_settings(data)
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO poller_settings(profile_id,settings_json,updated_at) VALUES(?,?,?)", (int(profile_id), json.dumps(settings), utcnow()))
    return settings


@dataclass
class ProfilePollState:
    profile_id: int
    last_fast_at: float = 0.0
    last_live_at: float = 0.0
    last_list_at: float = 0.0
    last_system_at: float = 0.0
    last_slow_at: float = 0.0
    last_tracker_at: float = 0.0
    last_disk_at: float = 0.0
    last_queue_at: float = 0.0
    last_heartbeat_at: float = 0.0
    last_ok: bool = True
    last_active: bool = False
    last_error: str = ""
    last_tick_ms: float = 0.0
    last_tick_started_at: float = 0.0
    last_tick_gap_ms: float = 0.0
    effective_interval_seconds: float = 0.0
    tick_count: int = 0
    sleep_hint: float = 1.0
    error_count: int = 0
    slow_count: int = 0
    skipped_emissions: int = 0
    emitted_payload_size: int = 0
    rtorrent_call_count: int = 0
    live_poll_count: int = 0
    list_poll_count: int = 0
    live_updated_total: int = 0
    live_full_refresh_requested_total: int = 0
    list_added_total: int = 0
    list_updated_total: int = 0
    list_removed_total: int = 0
    last_live_duration_ms: float = 0.0
    last_list_duration_ms: float = 0.0
    last_live_updated_count: int = 0
    last_list_added_count: int = 0
    last_list_updated_count: int = 0
    last_list_removed_count: int = 0
    last_live_ok: bool = True
    last_list_ok: bool = True
    last_live_error: str = ""
    last_list_error: str = ""
    last_live_requires_full_refresh: bool = False
    adaptive_mode: str = "normal"
    slow_task_running: bool = False
    system_task_running: bool = False
    stats: dict[str, Any] = field(default_factory=dict)


_STATES: dict[int, ProfilePollState] = {}


def state_for(profile_id: int) -> ProfilePollState:
    profile_id = int(profile_id)
    state = _STATES.get(profile_id)
    if state is None:
        state = ProfilePollState(profile_id=profile_id)
        _STATES[profile_id] = state
    return state


def interval_for(settings: dict, state: ProfilePollState) -> float:
    if not settings.get("adaptive_enabled"):
        return float(settings["active_interval_seconds"])
    if not state.last_ok:
        return float(settings["error_interval_seconds"])
    base = float(settings["active_interval_seconds"] if state.last_active else settings["idle_interval_seconds"])
    if state.adaptive_mode == "slowdown":
        return min(float(settings["error_interval_seconds"]), base * float(settings.get("slowdown_multiplier") or 2.0))
    return base


def effective_live_interval(settings: dict, state: ProfilePollState) -> float:
    return max(MIN_POLL_INTERVAL_SECONDS, interval_for(settings, state), float(settings.get("live_stats_interval_seconds") or DEFAULTS["live_stats_interval_seconds"]))


def effective_list_interval(settings: dict, state: ProfilePollState) -> float:
    return max(MIN_POLL_INTERVAL_SECONDS, float(settings.get("torrent_list_interval_seconds") or DEFAULTS["torrent_list_interval_seconds"]))


def effective_fast_interval(settings: dict, state: ProfilePollState) -> float:
    # Note: Kept for compatibility with older diagnostics; the fast interval now means lightweight live stats.
    return effective_live_interval(settings, state)


def should_live_poll(now: float, settings: dict, state: ProfilePollState) -> bool:
    return (now - state.last_live_at) >= effective_live_interval(settings, state)


def should_list_poll(now: float, settings: dict, state: ProfilePollState) -> bool:
    return (now - state.last_list_at) >= effective_list_interval(settings, state)


def should_fast_poll(now: float, settings: dict, state: ProfilePollState) -> bool:
    return should_live_poll(now, settings, state)


def should_system_poll(now: float, settings: dict, state: ProfilePollState) -> bool:
    return (now - state.last_system_at) >= float(settings["system_stats_interval_seconds"])


def should_slow_poll(now: float, settings: dict, state: ProfilePollState) -> bool:
    return (now - state.last_slow_at) >= float(settings["slow_stats_interval_seconds"])


def should_tracker_poll(now: float, settings: dict, state: ProfilePollState) -> bool:
    return (now - state.last_tracker_at) >= float(settings["tracker_stats_interval_seconds"])


def should_disk_poll(now: float, settings: dict, state: ProfilePollState) -> bool:
    return (now - state.last_disk_at) >= float(settings["disk_stats_interval_seconds"])


def should_queue_poll(now: float, settings: dict, state: ProfilePollState) -> bool:
    return (now - state.last_queue_at) >= float(settings["queue_stats_interval_seconds"])


def should_heartbeat(now: float, settings: dict, state: ProfilePollState, changed: bool) -> bool:
    if changed and settings.get("emit_heartbeat_on_change"):
        return True
    return (now - state.last_heartbeat_at) >= float(settings["heartbeat_interval_seconds"])


def mark_live_poll(state: ProfilePollState, started_at: float, ok: bool, error: str = "", updated_count: int = 0, requires_full_refresh: bool = False) -> None:
    now = time.monotonic()
    state.live_poll_count += 1
    state.last_live_duration_ms = round((now - started_at) * 1000.0, 2)
    state.last_live_updated_count = int(updated_count or 0)
    state.live_updated_total += int(updated_count or 0)
    state.last_live_requires_full_refresh = bool(requires_full_refresh)
    if requires_full_refresh:
        state.live_full_refresh_requested_total += 1
    state.last_live_ok = bool(ok)
    state.last_live_error = str(error or "")


def mark_list_poll(state: ProfilePollState, started_at: float, ok: bool, error: str = "", added_count: int = 0, updated_count: int = 0, removed_count: int = 0) -> None:
    now = time.monotonic()
    state.list_poll_count += 1
    state.last_list_duration_ms = round((now - started_at) * 1000.0, 2)
    state.last_list_added_count = int(added_count or 0)
    state.last_list_updated_count = int(updated_count or 0)
    state.last_list_removed_count = int(removed_count or 0)
    state.list_added_total += int(added_count or 0)
    state.list_updated_total += int(updated_count or 0)
    state.list_removed_total += int(removed_count or 0)
    state.last_list_ok = bool(ok)
    state.last_list_error = str(error or "")


def reset_runtime_stats(profile_id: int) -> dict:
    state = state_for(profile_id)
    state.tick_count = 0
    state.last_tick_ms = 0.0
    state.last_tick_gap_ms = 0.0
    state.last_tick_started_at = 0.0
    state.error_count = 0
    state.slow_count = 0
    state.skipped_emissions = 0
    state.emitted_payload_size = 0
    state.rtorrent_call_count = 0
    state.live_poll_count = 0
    state.list_poll_count = 0
    state.live_updated_total = 0
    state.live_full_refresh_requested_total = 0
    state.list_added_total = 0
    state.list_updated_total = 0
    state.list_removed_total = 0
    state.last_live_duration_ms = 0.0
    state.last_list_duration_ms = 0.0
    state.last_live_updated_count = 0
    state.last_list_added_count = 0
    state.last_list_updated_count = 0
    state.last_list_removed_count = 0
    state.last_live_ok = True
    state.last_list_ok = True
    state.last_live_error = ""
    state.last_list_error = ""
    state.last_live_requires_full_refresh = False
    state.stats = {}
    return snapshot(profile_id)


def mark_tick(state: ProfilePollState, started_at: float, active: bool, ok: bool, error: str = "", emitted_payload_size: int = 0, rtorrent_call_count: int = 0, skipped_emissions: int = 0, settings: dict | None = None) -> dict:
    now = time.monotonic()
    effective_settings = normalize_settings(settings) if settings is not None else DEFAULTS
    previous_started_at = state.last_tick_started_at
    state.tick_count += 1
    state.last_tick_ms = round((now - started_at) * 1000.0, 2)
    state.last_tick_gap_ms = round((started_at - previous_started_at) * 1000.0, 2) if previous_started_at else 0.0
    state.last_tick_started_at = started_at
    state.last_active = bool(active)
    state.effective_interval_seconds = effective_live_interval(effective_settings, state)
    state.last_ok = bool(ok)
    state.last_error = str(error or "")
    state.emitted_payload_size = int(emitted_payload_size or 0)
    state.rtorrent_call_count = int(rtorrent_call_count or 0)
    state.skipped_emissions += int(skipped_emissions or 0)
    adaptive_enabled = bool(effective_settings.get("adaptive_enabled", DEFAULTS["adaptive_enabled"]))

    if not adaptive_enabled:
        # Adaptive mode is explicitly disabled for this rTorrent profile. Keep metrics,
        # but do not enter slowdown/recovery or preserve a stale adaptive state from
        # earlier ticks; otherwise refreshes remain slow even with the toggle off.
        state.error_count = 0 if ok else state.error_count + 1
        state.slow_count = 0
        state.adaptive_mode = "fixed"
    else:
        if ok:
            state.error_count = 0
        else:
            state.error_count += 1
        threshold = float(effective_settings.get("slow_response_threshold_ms") or DEFAULTS["slow_response_threshold_ms"])
        recovery_after = int(effective_settings.get("recovery_after_errors") or DEFAULTS["recovery_after_errors"])
        if state.last_tick_ms >= threshold:
            state.slow_count += 1
            state.adaptive_mode = "slowdown"
        elif ok and state.error_count == 0 and state.slow_count:
            state.slow_count = max(0, state.slow_count - 1)
        if not ok and state.error_count >= recovery_after:
            state.adaptive_mode = "recovery"
        elif ok and state.slow_count == 0:
            state.adaptive_mode = "normal" if state.last_active else "idle"
    state.sleep_hint = max(MIN_POLL_INTERVAL_SECONDS, min(10.0, state.sleep_hint))
    state.stats = {
        "profile_id": state.profile_id,
        "tick_count": state.tick_count,
        "last_tick_ms": state.last_tick_ms,
        "last_active": state.last_active,
        "last_ok": state.last_ok,
        "last_tick_gap_ms": state.last_tick_gap_ms,
        "effective_interval_seconds": state.effective_interval_seconds,
        "live_stats_interval_seconds": effective_live_interval(effective_settings, state),
        "torrent_list_interval_seconds": effective_list_interval(effective_settings, state),
        "configured_min_interval_seconds": MIN_POLL_INTERVAL_SECONDS,
        "last_error": state.last_error,
        "duration_ms": state.last_tick_ms,
        "emitted_payload_size": state.emitted_payload_size,
        "rtorrent_call_count": state.rtorrent_call_count,
        "skipped_emissions": state.skipped_emissions,
        "adaptive_enabled": adaptive_enabled,
        "adaptive_mode": state.adaptive_mode,
        "error_count": state.error_count,
        "slow_count": state.slow_count,
        "live_poll_count": state.live_poll_count,
        "list_poll_count": state.list_poll_count,
        "last_live_duration_ms": state.last_live_duration_ms,
        "last_list_duration_ms": state.last_list_duration_ms,
        "last_live_updated_count": state.last_live_updated_count,
        "last_list_added_count": state.last_list_added_count,
        "last_list_updated_count": state.last_list_updated_count,
        "last_list_removed_count": state.last_list_removed_count,
        "live_updated_total": state.live_updated_total,
        "list_added_total": state.list_added_total,
        "list_updated_total": state.list_updated_total,
        "list_removed_total": state.list_removed_total,
        "live_full_refresh_requested_total": state.live_full_refresh_requested_total,
        "last_live_requires_full_refresh": state.last_live_requires_full_refresh,
        "last_live_ok": state.last_live_ok,
        "last_list_ok": state.last_list_ok,
        "last_live_error": state.last_live_error,
        "last_list_error": state.last_list_error,
        "updated_at": utcnow(),
    }
    return dict(state.stats)


def snapshot(profile_id: int, settings: dict | None = None) -> dict:
    state = state_for(profile_id)
    effective_settings = normalize_settings(settings) if settings is not None else get_settings(profile_id)
    data = dict(state.stats or {"profile_id": int(profile_id), "tick_count": state.tick_count})
    runtime_ready = bool(state.stats) or state.tick_count > 0
    data.setdefault("runtime_ready", runtime_ready)
    data.setdefault("adaptive_enabled", bool(effective_settings.get("adaptive_enabled", DEFAULTS["adaptive_enabled"])))
    data.setdefault("adaptive_mode", state.adaptive_mode if runtime_ready else ("fixed" if not data.get("adaptive_enabled") else "waiting"))
    data.setdefault("live_stats_interval_seconds", effective_live_interval(effective_settings, state))
    data.setdefault("torrent_list_interval_seconds", effective_list_interval(effective_settings, state))
    data.setdefault("configured_min_interval_seconds", MIN_POLL_INTERVAL_SECONDS)
    if not runtime_ready:
        data["last_ok"] = None
    data.update({
        "live_poll_count": state.live_poll_count,
        "list_poll_count": state.list_poll_count,
        "last_live_duration_ms": state.last_live_duration_ms,
        "last_list_duration_ms": state.last_list_duration_ms,
        "last_live_updated_count": state.last_live_updated_count,
        "last_list_added_count": state.last_list_added_count,
        "last_list_updated_count": state.last_list_updated_count,
        "last_list_removed_count": state.last_list_removed_count,
        "live_updated_total": state.live_updated_total,
        "list_added_total": state.list_added_total,
        "list_updated_total": state.list_updated_total,
        "list_removed_total": state.list_removed_total,
        "live_full_refresh_requested_total": state.live_full_refresh_requested_total,
        "last_live_requires_full_refresh": state.last_live_requires_full_refresh,
        "last_live_ok": state.last_live_ok,
        "last_list_ok": state.last_list_ok,
        "last_live_error": state.last_live_error,
        "last_list_error": state.last_list_error,
    })
    return data
