from __future__ import annotations
import json
import threading
import time
import psutil
from datetime import datetime, timezone
from typing import Any
from ..db import connect, default_user_id, utcnow
from . import auth, operation_logs, rtorrent

PLANNER_STARTUP_DELAY_SECONDS = 60
_APP_STARTED_AT = time.monotonic()

DEFAULTS = {
    "enabled": False,
    "name": "Default download plan",
    "profile_name": "night mode",
    "dry_run": False,
    "manual_override_until": "",
    "night_only_enabled": False,
    "night_start": "23:00",
    "night_end": "07:00",
    "quiet_hours_enabled": False,
    "quiet_start": "22:00",
    "quiet_end": "06:00",
    "weekday_down": 0,
    "weekday_up": 0,
    "weekend_down": 0,
    "weekend_up": 0,
    "hourly_schedule_enabled": False,
    "hourly_schedule": [],
    "auto_pause_cpu_enabled": False,
    "auto_pause_cpu_percent": 90,
    "auto_pause_disk_enabled": False,
    "auto_pause_disk_percent": 95,
    "network_protection_enabled": False,
    "network_max_down": 0,
    "network_max_up": 0,
    "load_protection_enabled": False,
    "load_cpu_percent": 95,
    "auto_resume": True,
    "auto_resume_grace_seconds": 0,
    "check_interval_seconds": 30,
}

_LAST_RUN: dict[int, float] = {}
_LAST_LIMITS: dict[int, tuple[int, int]] = {}
_HIGH_CPU_SINCE: dict[int, float] = {}
_PLANNER_CONNECTION_STATUS: dict[int, str] = {}
_SCHEDULER_STARTED = False
_SCHEDULER_LOCK = threading.Lock()
_PROFILE_LOCKS: dict[int, threading.Lock] = {}
_PROFILE_LOCKS_GUARD = threading.Lock()


def _profile_lock(profile_id: int) -> threading.Lock:
    """Keep one planner run per profile active at a time."""
    with _PROFILE_LOCKS_GUARD:
        if profile_id not in _PROFILE_LOCKS:
            _PROFILE_LOCKS[profile_id] = threading.Lock()
        return _PROFILE_LOCKS[profile_id]


def _all_profiles() -> list[dict]:
    """Read every configured profile directly from DB for browser-independent background work."""
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM rtorrent_profiles ORDER BY id").fetchall()]


def _owner_user_id(profile: dict) -> int:
    """Use the profile owner for background planner checks."""
    return int(profile.get("user_id") or default_user_id())


def _rtorrent_ready(profile: dict) -> tuple[bool, str]:
    """Check rTorrent connectivity before the planner evaluates or applies changes."""
    try:
        rtorrent.client_for(profile).call("system.client_version")
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _log_connection_status(profile: dict, status: str, message: str, *, error: str = "", user_id: int | None = None) -> None:
    """Record planner connectivity state changes as system operations without noisy repeats."""
    profile_id = int(profile.get("id") or 0)
    if _PLANNER_CONNECTION_STATUS.get(profile_id) == status:
        return
    _PLANNER_CONNECTION_STATUS[profile_id] = status
    operation_logs.record(
        profile_id,
        "download_planner_status",
        message,
        severity="warning" if error else "info",
        source="system",
        action="download_planner",
        details={"status": status, "error": error},
        user_id=user_id or int(profile.get("user_id") or 0) or None,
    )


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int(value: Any, default: int = 0, lo: int = 0, hi: int = 10**9) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except Exception:
        return default


def _hourly_schedule(value: Any) -> list[dict]:
    rows = value if isinstance(value, list) else []
    by_hour: dict[int, dict] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            hour = int(item.get("hour"))
        except Exception:
            continue
        if hour < 0 or hour > 23:
            continue
        by_hour[hour] = {"hour": hour, "down": _int(item.get("down"), 0), "up": _int(item.get("up"), 0)}
    return [by_hour.get(hour, {"hour": hour, "down": 0, "up": 0}) for hour in range(24)]

def _hourly_limit_for(settings: dict, hour: int) -> tuple[int, int] | None:
    if not settings.get("hourly_schedule_enabled"):
        return None
    rows = settings.get("hourly_schedule") or []
    for item in rows:
        if int(item.get("hour", -1)) == int(hour):
            return int(item.get("down") or 0), int(item.get("up") or 0)
    return 0, 0


def _time_minutes(value: str, fallback: str) -> int:
    text = str(value or fallback).strip()
    try:
        hh, mm = text.split(":", 1)
        return max(0, min(1439, int(hh) * 60 + int(mm)))
    except Exception:
        hh, mm = fallback.split(":", 1)
        return int(hh) * 60 + int(mm)


def _in_window(now_min: int, start: str, end: str) -> bool:
    s = _time_minutes(start, "00:00")
    e = _time_minutes(end, "00:00")
    if s == e:
        return True
    if s < e:
        return s <= now_min < e
    return now_min >= s or now_min < e


def normalize(data: dict | None) -> dict:
    raw = {**DEFAULTS, **(data or {})}
    return {
        "enabled": _bool(raw.get("enabled")),
        "name": str(raw.get("name") or DEFAULTS["name"]).strip()[:120],
        "profile_name": str(raw.get("profile_name") or raw.get("name") or DEFAULTS["profile_name"]).strip()[:80],
        "dry_run": _bool(raw.get("dry_run")),
        "manual_override_until": str(raw.get("manual_override_until") or "")[:40],
        "night_only_enabled": _bool(raw.get("night_only_enabled")),
        "night_start": str(raw.get("night_start") or DEFAULTS["night_start"])[:5],
        "night_end": str(raw.get("night_end") or DEFAULTS["night_end"])[:5],
        "quiet_hours_enabled": _bool(raw.get("quiet_hours_enabled")),
        "quiet_start": str(raw.get("quiet_start") or DEFAULTS["quiet_start"])[:5],
        "quiet_end": str(raw.get("quiet_end") or DEFAULTS["quiet_end"])[:5],
        "weekday_down": _int(raw.get("weekday_down"), 0),
        "weekday_up": _int(raw.get("weekday_up"), 0),
        "weekend_down": _int(raw.get("weekend_down"), 0),
        "weekend_up": _int(raw.get("weekend_up"), 0),
        "hourly_schedule_enabled": _bool(raw.get("hourly_schedule_enabled")),
        "hourly_schedule": _hourly_schedule(raw.get("hourly_schedule")),
        "auto_pause_cpu_enabled": _bool(raw.get("auto_pause_cpu_enabled")),
        "auto_pause_cpu_percent": _int(raw.get("auto_pause_cpu_percent"), 90, 1, 100),
        "auto_pause_disk_enabled": _bool(raw.get("auto_pause_disk_enabled")),
        "auto_pause_disk_percent": _int(raw.get("auto_pause_disk_percent"), 95, 1, 100),
        "network_protection_enabled": _bool(raw.get("network_protection_enabled")),
        "network_max_down": _int(raw.get("network_max_down"), 0),
        "network_max_up": _int(raw.get("network_max_up"), 0),
        "load_protection_enabled": _bool(raw.get("load_protection_enabled")),
        "load_cpu_percent": _int(raw.get("load_cpu_percent"), 95, 1, 100),
        "auto_resume": _bool(raw.get("auto_resume")),
        "auto_resume_grace_seconds": _int(raw.get("auto_resume_grace_seconds"), 0, 0, 86400),
        "check_interval_seconds": _int(raw.get("check_interval_seconds"), 30, 10, 3600),
    }


def _row(user_id: int | None, profile_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM download_plan_settings WHERE profile_id=? ORDER BY updated_at DESC, user_id ASC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if row:
            return row
        if user_id:
            return conn.execute(
                "SELECT * FROM download_plan_settings WHERE user_id=? AND profile_id=?",
                (user_id, profile_id),
            ).fetchone()
    return None


def _user_label(user_id: int | None) -> str:
    if not user_id:
        return "system"
    with connect() as conn:
        row = conn.execute("SELECT display_name, username, email FROM users WHERE id=?", (int(user_id),)).fetchone()
    if row:
        return str(row.get("display_name") or row.get("username") or row.get("email") or f"user {user_id}")
    return f"user {user_id}"



def _preference_row_for_disk_source(profile_id: int, user_id: int | None = None) -> dict | None:
    from . import preferences
    user_id = user_id or default_user_id()
    return preferences.get_disk_monitor_preferences(profile_id, user_id)

def _legacy_disk_guard_defaults(profile_id: int, user_id: int | None = None) -> dict:
    pref = _preference_row_for_disk_source(profile_id, user_id)
    if not pref or not pref.get("disk_monitor_stop_enabled"):
        return {}
    return {
        "enabled": True,
        "auto_pause_disk_enabled": True,
        "auto_pause_disk_percent": _int(pref.get("disk_monitor_stop_threshold"), 95, 1, 100),
        "auto_resume": True,
    }


def _history_key(profile_id: int) -> str:
    return f"download_planner.history.{int(profile_id)}"


def _override_key(profile_id: int) -> str:
    return f"download_planner.override_until.{int(profile_id)}"


def _parse_iso_ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0


def _override_until(profile_id: int) -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (_override_key(profile_id),)).fetchone()
    return str(row.get("value") or "") if row else ""


def set_manual_override(profile_id: int, seconds: int) -> dict:
    until = ""
    seconds = _int(seconds, 0, 0, 86400)
    if seconds:
        until = datetime.fromtimestamp(time.time() + seconds, tz=timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)", (_override_key(profile_id), until))
    return {"manual_override_until": until, "seconds": seconds}


def _append_history(profile_id: int, event: str, payload: dict | None = None) -> None:
    payload = payload or {}
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (_history_key(profile_id),)).fetchone()
        try:
            items = json.loads(row.get("value") or "[]") if row else []
        except Exception:
            items = []
        items.append({"at": utcnow(), "event": str(event), **payload})
        items = items[-80:]
        conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)", (_history_key(profile_id), json.dumps(items)))


def _history_items(profile_id: int) -> list[dict]:
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (_history_key(profile_id),)).fetchone()
    try:
        items = json.loads(row.get("value") or "[]") if row else []
    except Exception:
        items = []
    return items if isinstance(items, list) else []


def history(profile_id: int, limit: int = 40) -> list[dict]:
    items = _history_items(profile_id)
    return list(reversed(items[-max(1, min(200, int(limit))):]))


def history_count(profile_id: int) -> int:
    return len(_history_items(profile_id))


def clear_history(profile_id: int) -> int:
    deleted = history_count(profile_id)
    with connect() as conn:
        # Note: Planner history is stored per profile in app_settings; clearing it does not change saved Planner rules.
        conn.execute("DELETE FROM app_settings WHERE key=?", (_history_key(profile_id),))
    return deleted


def _profile_label(settings: dict) -> str:
    return str(settings.get("profile_name") or settings.get("name") or "Planner")


def _next_boundary(now: datetime, settings: dict) -> str:
    candidates: list[datetime] = []
    for hour in range(24):
        if settings.get("hourly_schedule_enabled"):
            dt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if dt <= now:
                dt = dt + __import__("datetime").timedelta(days=1)
            candidates.append(dt)
    for key in ("night_start", "night_end", "quiet_start", "quiet_end"):
        value = settings.get(key)
        if not value:
            continue
        minute = _time_minutes(str(value), "00:00")
        dt = now.replace(hour=minute // 60, minute=minute % 60, second=0, microsecond=0)
        if dt <= now:
            dt = dt.replace(day=dt.day) + __import__("datetime").timedelta(days=1)
        candidates.append(dt)
    return min(candidates).isoformat() if candidates else ""

def get_settings(profile_id: int, user_id: int | None = None) -> dict:
    user_id = user_id or default_user_id()
    row = _row(user_id, profile_id)
    if not row:
        migrated = normalize({**DEFAULTS, **_legacy_disk_guard_defaults(int(profile_id), user_id)})
        return {**migrated, "profile_id": int(profile_id), "owner_user_id": int(user_id), "owner_name": _user_label(user_id)}
    try:
        data = json.loads(row.get("settings_json") or "{}")
    except Exception:
        data = {}
    owner_user_id = int(row.get("user_id") or user_id)
    settings = {**normalize(data), "profile_id": int(profile_id), "owner_user_id": owner_user_id, "owner_name": _user_label(owner_user_id), "updated_at": row.get("updated_at")}
    runtime_override = _override_until(int(profile_id))
    if runtime_override:
        settings["manual_override_until"] = runtime_override
    return settings


def save_settings(profile_id: int, data: dict, user_id: int | None = None) -> dict:
    user_id = user_id or default_user_id()
    if not auth.can_write_profile(int(profile_id), user_id):
        raise PermissionError("No write access to profile")
    settings = normalize(data)
    now = utcnow()
    with connect() as conn:
        conn.execute("DELETE FROM download_plan_settings WHERE profile_id=?", (int(profile_id),))
        conn.execute(
            """
            INSERT INTO download_plan_settings(user_id, profile_id, settings_json, updated_at)
            VALUES(?,?,?,?)
            """,
            (user_id, profile_id, json.dumps(settings), now),
        )
    return {**settings, "profile_id": int(profile_id), "owner_user_id": int(user_id), "owner_name": _user_label(user_id), "updated_at": now}


def _active_downloading_hashes(profile: dict) -> list[str]:
    rows = rtorrent.list_torrents(profile)
    hashes: list[str] = []
    for row in rows:
        if int(row.get("complete") or 0):
            continue
        if int(row.get("state") or 0) and not row.get("paused") and str(row.get("status") or "") != "Queued":
            h = str(row.get("hash") or "")
            if h:
                hashes.append(h)
    return hashes


def _remember_paused(profile_id: int, hashes: list[str], reason: str) -> None:
    if not hashes:
        return
    now = utcnow()
    with connect() as conn:
        for h in hashes:
            conn.execute(
                "INSERT OR REPLACE INTO download_plan_paused(profile_id,torrent_hash,reason,created_at,updated_at) VALUES(?,?,?,?,?)",
                (profile_id, h, reason, now, now),
            )


def _planned_paused(profile_id: int) -> list[str]:
    with connect() as conn:
        rows = conn.execute("SELECT torrent_hash FROM download_plan_paused WHERE profile_id=?", (profile_id,)).fetchall()
    return [str(row.get("torrent_hash") or "") for row in rows if row.get("torrent_hash")]


def _clear_planned(profile_id: int, hashes: list[str] | None = None) -> None:
    with connect() as conn:
        if hashes:
            conn.executemany("DELETE FROM download_plan_paused WHERE profile_id=? AND torrent_hash=?", [(profile_id, h) for h in hashes])
        else:
            conn.execute("DELETE FROM download_plan_paused WHERE profile_id=?", (profile_id,))


def disk_usage(profile: dict, user_id: int | None = None) -> dict | None:
    profile_id = int(profile.get("id") or 0)
    pref = _preference_row_for_disk_source(profile_id, user_id) or {}
    try:
        paths = json.loads(pref.get("disk_monitor_paths_json") or "[]")
    except Exception:
        paths = []
    if not isinstance(paths, list):
        paths = []
    try:
        return rtorrent.disk_usage_for_paths(
            profile,
            [str(p) for p in paths if str(p or "").strip()],
            str(pref.get("disk_monitor_mode") or "default"),
            str(pref.get("disk_monitor_selected_path") or ""),
        )
    except Exception:
        return None


def _disk_percent(profile: dict, user_id: int | None = None) -> float | None:
    usage = disk_usage(profile, user_id)
    if usage and usage.get("ok"):
        return float(usage.get("percent") or 0)
    return None


def evaluate(profile: dict, settings: dict | None = None, now: datetime | None = None) -> dict:
    settings = normalize(settings or get_settings(int(profile.get("id") or 0)))
    now = now or datetime.now().astimezone()
    override_until = settings.get("manual_override_until") or _override_until(int(profile.get("id") or 0))
    override_active = bool(_parse_iso_ts(override_until) > time.time())
    now_min = now.hour * 60 + now.minute
    weekend = now.weekday() >= 5
    reasons: list[str] = []
    pause_downloads = False
    quiet = bool(settings["quiet_hours_enabled"] and _in_window(now_min, settings["quiet_start"], settings["quiet_end"]))
    in_night = _in_window(now_min, settings["night_start"], settings["night_end"])
    if quiet:
        pause_downloads = True
        reasons.append("quiet_hours")
    if settings["night_only_enabled"] and not in_night:
        pause_downloads = True
        reasons.append("outside_night_window")
    hourly_limits = _hourly_limit_for(settings, now.hour)
    if hourly_limits is not None:
        down, up = hourly_limits
        reasons.append("hourly_schedule")
    else:
        down = int(settings["weekend_down"] if weekend else settings["weekday_down"])
        up = int(settings["weekend_up"] if weekend else settings["weekday_up"])
    if quiet or pause_downloads:
        down = 0
    cpu = None
    if settings["load_protection_enabled"]:
        cpu_load = float(psutil.cpu_percent(interval=None))
        if cpu_load >= float(settings["load_cpu_percent"]):
            pause_downloads = True
            reasons.append("high_load")
    if settings["auto_pause_cpu_enabled"]:
        cpu = float(psutil.cpu_percent(interval=None))
        pid = int(profile.get("id") or 0)
        if cpu >= float(settings["auto_pause_cpu_percent"]):
            _HIGH_CPU_SINCE.setdefault(pid, time.monotonic())
            if time.monotonic() - _HIGH_CPU_SINCE[pid] >= 10:
                pause_downloads = True
                reasons.append("high_cpu")
        else:
            _HIGH_CPU_SINCE.pop(pid, None)
    disk = None
    if settings["auto_pause_disk_enabled"]:
        disk = _disk_percent(profile, int(settings.get("user_id") or default_user_id()))
        if disk is not None and disk >= float(settings["auto_pause_disk_percent"]):
            pause_downloads = True
            reasons.append("high_disk")
    if settings["network_protection_enabled"]:
        nd = int(settings.get("network_max_down") or 0)
        nu = int(settings.get("network_max_up") or 0)
        if nd and (not down or down > nd):
            down = nd
            reasons.append("network_limit_down")
        if nu and (not up or up > nu):
            up = nu
            reasons.append("network_limit_up")
    if override_active:
        pause_downloads = False
        reasons = ["manual_override"]
    return {
        "enabled": bool(settings["enabled"]),
        "profile_id": int(profile.get("id") or 0),
        "profile_name": _profile_label(settings),
        "dry_run": bool(settings.get("dry_run")),
        "manual_override_until": override_until if override_active else "",
        "matched_rule": reasons[0] if reasons else ("weekend" if weekend else "weekday"),
        "next_change_at": _next_boundary(now, settings),
        "pause_downloads": pause_downloads,
        "reasons": reasons,
        "down": down,
        "up": up,
        "weekend": weekend,
        "quiet": quiet,
        "in_night_window": in_night,
        "cpu": cpu,
        "disk": disk,
    }


def enforce(profile: dict, force: bool = False, user_id: int | None = None) -> dict:
    profile_id = int(profile.get("id") or 0)
    settings = get_settings(profile_id, user_id or int(profile.get("user_id") or default_user_id()))
    user_id = int(settings.get("owner_user_id") or user_id or profile.get("user_id") or default_user_id())
    if not auth.can_write_profile(profile_id, user_id):
        return {"ok": True, "enabled": False, "profile_id": profile_id, "skipped": True, "reason": "planner owner has no write access", "history": history(profile_id, 20), "history_total": history_count(profile_id)}
    if not settings.get("enabled"):
        return {"ok": True, "enabled": False, "profile_id": profile_id, "history": history(profile_id, 20), "history_total": history_count(profile_id), "preview": preview(profile, user_id=user_id)}
    startup_remaining = int(PLANNER_STARTUP_DELAY_SECONDS - (time.monotonic() - _APP_STARTED_AT))
    if not force and startup_remaining > 0:
        # Note: The background planner keeps the same startup grace as rTorrent config apply, while manual checks still run immediately.
        return {"ok": True, "enabled": True, "profile_id": profile_id, "skipped": True, "reason": "startup_delay", "retry_after_seconds": startup_remaining}
    now = time.monotonic()
    interval = int(settings.get("check_interval_seconds") or 30)
    if not force and now - _LAST_RUN.get(profile_id, 0) < interval:
        return {"ok": True, "enabled": True, "profile_id": profile_id, "skipped": True}
    _LAST_RUN[profile_id] = now
    ready, connection_error = _rtorrent_ready(profile)
    if not ready:
        _log_connection_status(profile, "waiting", f"Download Planner is waiting for rTorrent: {connection_error}", error=connection_error, user_id=user_id)
        return {"ok": True, "enabled": True, "profile_id": profile_id, "skipped": True, "reason": "rtorrent_unavailable", "error": connection_error, "retry_after_seconds": interval}
    _log_connection_status(profile, "connected", "Download Planner detected a working rTorrent connection", user_id=user_id)
    decision = evaluate(profile, settings)
    result: dict[str, Any] = {"ok": True, "enabled": True, **decision, "limits_changed": False, "paused": 0, "resumed": 0}
    wanted_limits = (int(decision["down"]), int(decision["up"]))
    dry_run = bool(settings.get("dry_run")) or bool(force and str(profile.get("dry_run") or "").lower() == "true")
    result["dry_run"] = dry_run
    if force or _LAST_LIMITS.get(profile_id) != wanted_limits:
        if not dry_run:
            rtorrent.set_limits(profile, wanted_limits[0], wanted_limits[1])
            _LAST_LIMITS[profile_id] = wanted_limits
        result["limits_changed"] = True
        _append_history(profile_id, "speed_limit_change", {"down": wanted_limits[0], "up": wanted_limits[1], "dry_run": dry_run})
    if decision["pause_downloads"]:
        hashes = _active_downloading_hashes(profile)
        if hashes:
            action = {"dry_run": True} if dry_run else rtorrent.action(profile, hashes, "pause", {"source": "download_planner", "reasons": decision["reasons"]})
            if not dry_run:
                _remember_paused(profile_id, hashes, ",".join(decision["reasons"]))
            result["paused"] = len(hashes)
            result["pause_result"] = action
            _append_history(profile_id, "paused_torrents", {"count": len(hashes), "reasons": decision["reasons"], "dry_run": dry_run})
            if "high_cpu" in decision["reasons"] or "high_load" in decision["reasons"]:
                _append_history(profile_id, "cpu_protection_trigger", {"cpu": decision.get("cpu"), "dry_run": dry_run})
            if "high_disk" in decision["reasons"]:
                _append_history(profile_id, "disk_protection_trigger", {"disk": decision.get("disk"), "dry_run": dry_run})
    elif settings.get("auto_resume"):
        grace = int(settings.get("auto_resume_grace_seconds") or 0)
        last_trigger = 0.0
        for item in history(profile_id, 20):
            if item.get("event") in {"paused_torrents", "cpu_protection_trigger", "disk_protection_trigger"}:
                last_trigger = _parse_iso_ts(item.get("at"))
                break
        if grace and last_trigger and time.time() - last_trigger < grace:
            result["resume_wait_seconds"] = int(grace - (time.time() - last_trigger))
        else:
            hashes = _planned_paused(profile_id)
            if hashes:
                action = {"dry_run": True} if dry_run else rtorrent.action(profile, hashes, "resume", {"source": "download_planner"})
                if not dry_run:
                    _clear_planned(profile_id, hashes)
                result["resumed"] = len(hashes)
                result["resume_result"] = action
                _append_history(profile_id, "resumed_torrents", {"count": len(hashes), "dry_run": dry_run})
    result["history"] = history(profile_id, 20)
    result["history_total"] = history_count(profile_id)
    result["preview"] = preview(profile, user_id=user_id)
    return result


def preview(profile: dict, user_id: int | None = None) -> dict:
    profile_id = int(profile.get("id") or 0)
    settings = get_settings(profile_id, user_id or int(profile.get("user_id") or default_user_id()))
    decision = evaluate(profile, settings)
    return {
        "profile_id": profile_id,
        "profile_name": decision.get("profile_name"),
        "matched_rule": decision.get("matched_rule"),
        "next_change_at": decision.get("next_change_at"),
        "pause_downloads": decision.get("pause_downloads"),
        "down": decision.get("down"),
        "up": decision.get("up"),
        "reasons": decision.get("reasons", []),
        "manual_override_until": decision.get("manual_override_until", ""),
        "dry_run": decision.get("dry_run", False),
    }


def start_scheduler(socketio=None) -> None:
    """Start the browser-independent planner loop for every configured profile."""
    global _SCHEDULER_STARTED
    with _SCHEDULER_LOCK:
        if _SCHEDULER_STARTED:
            return
        _SCHEDULER_STARTED = True

    def loop():
        while True:
            try:
                from .websocket import emit_profile_event
                for profile in _all_profiles():
                    profile_id = int(profile.get("id") or 0)
                    if not profile_id:
                        continue
                    lock = _profile_lock(profile_id)
                    if not lock.acquire(blocking=False):
                        continue
                    try:
                        # Note: Background planner runs per configured profile with the profile owner, not only for the active UI profile.
                        result = enforce(profile, force=False, user_id=_owner_user_id(profile))
                        if socketio and result.get("enabled") and not result.get("skipped"):
                            emit_profile_event(socketio, "download_plan_update", result, profile_id)
                    except Exception as exc:
                        if socketio:
                            emit_profile_event(socketio, "download_plan_update", {"ok": False, "profile_id": profile_id, "error": str(exc)}, profile_id)
                    finally:
                        lock.release()
            except Exception:
                pass
            if socketio:
                socketio.sleep(30)
            else:
                time.sleep(30)

    if socketio:
        socketio.start_background_task(loop)
    else:
        threading.Thread(target=loop, daemon=True, name="pytorrent-download-planner-scheduler").start()
