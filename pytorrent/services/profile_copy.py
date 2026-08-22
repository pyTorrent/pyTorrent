from __future__ import annotations

import json
from typing import Any

from ..db import connect, default_user_id, utcnow
from . import auth, automation_rules, backup, download_planner, operation_logs, poller_control, preferences, smart_queue
from .rtorrent import config as rtorrent_config


PREFERENCE_COPY_SCOPES: dict[str, tuple[str, ...]] = {
    "view_state": (
        "torrent_sort_json",
        "active_filter",
        "sidebar_labels_expanded",
        "sidebar_shortcuts_expanded",
    ),
    "tracker_favicons": ("tracker_favicons_enabled",),
    "port_checker": ("port_check_enabled",),
    "peers": ("peers_refresh_seconds", "reverse_dns_enabled"),
    "columns": ("table_columns_json",),
    "footer": (
        "footer_items_json",
        "footer_order_json",
        "system_usage_chart_mode",
        "system_usage_chart_expanded",
    ),
}

BASIC_PROFILE_PREFERENCE_SCOPES = (
    "view_state",
    "tracker_favicons",
    "port_checker",
    "peers",
    "columns",
    "footer",
)
BASIC_PROFILE_PREFERENCE_COLUMNS = tuple(
    dict.fromkeys(
        column
        for scope in BASIC_PROFILE_PREFERENCE_SCOPES
        for column in PREFERENCE_COPY_SCOPES[scope]
        if column != "active_filter"
    )
)

SMART_QUEUE_COPY_KEYS = (
    "enabled",
    "max_active_downloads",
    "stalled_seconds",
    "min_speed_bytes",
    "min_seeds",
    "min_peers",
    "ignore_seed_peer",
    "ignore_speed",
    "manage_stopped",
    "cooldown_minutes",
    "refill_enabled",
    "refill_interval_minutes",
    "surge_refill_enabled",
    "surge_refill_interval_minutes",
    "surge_refill_batch_size",
    "stop_batch_size",
    "start_grace_seconds",
    "protect_active_below_cap",
    "prefer_partial_progress",
    "auto_stop_idle",
)


def _actor_id(user_id: int | None = None) -> int:
    """Resolve the user that owns copied user-scoped configuration."""
    # Note: Profile-copy writes always belong to the signed-in user, while auth-disabled installs keep using the built-in user.
    return int(user_id or auth.current_user_id() or default_user_id())


def _validate_profiles(source_profile_id: int, target_profile_id: int, user_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate source read access and target write access before copying anything."""
    # Note: Source access is read-only by design; only the active destination profile needs write permission.
    source_id = int(source_profile_id or 0)
    target_id = int(target_profile_id or 0)
    if not source_id or not target_id:
        raise ValueError("Source and target profile are required")
    if source_id == target_id:
        raise ValueError("Choose a different source profile")
    if not auth.can_access_profile(source_id, user_id):
        raise PermissionError("No access to source profile")
    if not auth.can_write_profile(target_id, user_id):
        raise PermissionError("No write access to target profile")
    source = preferences.get_profile(source_id, user_id)
    target = preferences.get_profile(target_id, user_id)
    if not source or not target:
        raise ValueError("Profile not found")
    return dict(source), dict(target)


def copy_preference_scope(source_profile_id: int, target_profile_id: int, scope: str, user_id: int | None = None) -> dict[str, Any]:
    """Copy one profile-scoped Preferences section without touching user-global settings."""
    # Note: Each Preferences button maps to a narrow column set so copying Footer cannot overwrite Columns, Port checker, or other sections.
    actor_id = _actor_id(user_id)
    _validate_profiles(source_profile_id, target_profile_id, actor_id)
    clean_scope = str(scope or "").strip().lower()
    if clean_scope == "disk_monitor":
        source = preferences.get_disk_monitor_preferences(int(source_profile_id), actor_id)
        payload = {
            key: source.get(key)
            for key in (
                "disk_monitor_paths_json",
                "disk_monitor_mode",
                "disk_monitor_selected_path",
                "disk_monitor_stop_enabled",
                "disk_monitor_stop_threshold",
            )
        }
        copied = preferences.save_disk_monitor_preferences(int(target_profile_id), payload, actor_id)
        return {"scope": clean_scope, "copied": 1, "preferences": copied}
    columns = PREFERENCE_COPY_SCOPES.get(clean_scope)
    if not columns:
        raise ValueError("Unsupported Preferences copy scope")
    source = preferences.get_profile_preferences(actor_id, int(source_profile_id))
    payload = {column: source.get(column) for column in columns if column in source}
    preferences.save_profile_preferences(actor_id, int(target_profile_id), payload)
    return {
        "scope": clean_scope,
        "copied": len(payload),
        "preferences": preferences.get_preferences(actor_id, int(target_profile_id)),
    }


def copy_basic_profile_settings(source_profile_id: int, target_profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    """Copy the normal profile UI preferences without technical or path-specific configuration."""
    # Note: This preset intentionally excludes Disk Monitor, download paths, automation tools, daemon tuning, backups and runtime state; the current filter also stays local because label/tracker filters may not exist on the target profile.
    actor_id = _actor_id(user_id)
    _validate_profiles(source_profile_id, target_profile_id, actor_id)
    source = preferences.get_profile_preferences(actor_id, int(source_profile_id))
    payload = {
        column: source.get(column)
        for column in BASIC_PROFILE_PREFERENCE_COLUMNS
        if column in source
    }
    preferences.save_profile_preferences(actor_id, int(target_profile_id), payload)
    return {
        "scope": "profile_basics",
        "copied": len(payload),
        "sections": list(BASIC_PROFILE_PREFERENCE_SCOPES),
        "preferences": preferences.get_preferences(actor_id, int(target_profile_id)),
    }


def _portable_rule_signature(rule: dict[str, Any]) -> str:
    """Return a stable signature used to avoid duplicate automation copies."""
    # Note: Runtime ids, owners, timestamps, cooldown state and history never participate in copy de-duplication.
    portable = {
        "name": str(rule.get("name") or "Automation rule"),
        "enabled": bool(rule.get("enabled", True)),
        "cooldown_minutes": max(0, int(rule.get("cooldown_minutes") or 0)),
        "conditions": rule.get("conditions") or [],
        "effects": rule.get("effects") or [],
    }
    return json.dumps(portable, sort_keys=True, separators=(",", ":"))


def copy_automations(source_profile_id: int, target_profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    """Copy automation rule definitions while leaving execution state and history behind."""
    # Note: Exact duplicates already present in the target profile are skipped, making repeated copies safe and non-destructive.
    actor_id = _actor_id(user_id)
    _validate_profiles(source_profile_id, target_profile_id, actor_id)
    source_rules = automation_rules.list_rules(int(source_profile_id), user_id=actor_id)
    target_rules = automation_rules.list_rules(int(target_profile_id), user_id=actor_id)
    existing = {_portable_rule_signature(rule) for rule in target_rules}
    portable_rules = []
    for rule in source_rules:
        signature = _portable_rule_signature(rule)
        if signature in existing:
            continue
        existing.add(signature)
        portable_rules.append({
            "name": str(rule.get("name") or "Automation rule"),
            "enabled": bool(rule.get("enabled", True)),
            "cooldown_minutes": max(0, int(rule.get("cooldown_minutes") or 0)),
            "conditions": list(rule.get("conditions") or []),
            "effects": list(rule.get("effects") or []),
        })
    if portable_rules:
        automation_rules.import_rules(
            int(target_profile_id),
            {"version": 1, "scope": "profile", "rules": portable_rules},
            user_id=actor_id,
            replace=False,
        )
    return {"scope": "automations", "copied": len(portable_rules), "skipped": len(source_rules) - len(portable_rules)}


def copy_smart_queue(source_profile_id: int, target_profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    """Copy Smart Queue settings without copying torrent-specific state or operation history."""
    # Note: Timers, exclusions, stalled hashes and historical runs stay local to the destination profile.
    actor_id = _actor_id(user_id)
    _validate_profiles(source_profile_id, target_profile_id, actor_id)
    source = smart_queue.get_settings(int(source_profile_id), actor_id)
    payload = {key: source.get(key) for key in SMART_QUEUE_COPY_KEYS}
    if int(source.get("refill_enabled") or 0) == 0:
        payload["refill_mode"] = "off"
    elif int(source.get("refill_interval_minutes") or 0) > 0:
        payload["refill_mode"] = "custom"
    else:
        payload["refill_mode"] = "auto"
    copied = smart_queue.save_settings(int(target_profile_id), payload, actor_id)
    return {"scope": "smart_queue", "copied": len(SMART_QUEUE_COPY_KEYS), "settings": copied}




def copy_backup_schedule(source_profile_id: int, target_profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    """Copy the automatic profile-backup schedule without copying backup files or last-run state."""
    # Note: Schedule policy is portable, while existing backups and scheduler progress remain attached to their original profiles.
    actor_id = _actor_id(user_id)
    _validate_profiles(source_profile_id, target_profile_id, actor_id)
    source = backup.get_auto_backup_settings(actor_id, "profile", int(source_profile_id))
    payload = {
        "enabled": bool(source.get("enabled")),
        "interval_hours": int(source.get("interval_hours") or 24),
        "retention_days": int(source.get("retention_days") or 30),
    }
    copied = backup.save_auto_backup_settings(payload, actor_id, "profile", int(target_profile_id))
    return {"scope": "backup_schedule", "copied": len(payload), "settings": copied}


def copy_job_scheduling(source_profile_id: int, target_profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    """Copy only worker concurrency and timeout settings between rTorrent profiles."""
    # Note: Connection URL, profile name, default flag and remote/local mode remain untouched because they identify the destination daemon itself.
    actor_id = _actor_id(user_id)
    source, _ = _validate_profiles(source_profile_id, target_profile_id, actor_id)
    fields = (
        "max_parallel_jobs",
        "light_parallel_jobs",
        "light_job_timeout_seconds",
        "heavy_job_timeout_seconds",
        "pending_job_timeout_seconds",
    )
    values = [source.get(field) for field in fields]
    with connect() as conn:
        conn.execute(
            "UPDATE rtorrent_profiles SET max_parallel_jobs=?,light_parallel_jobs=?,light_job_timeout_seconds=?,heavy_job_timeout_seconds=?,pending_job_timeout_seconds=?,updated_at=? WHERE id=?",
            (*values, utcnow(), int(target_profile_id)),
        )
    return {"scope": "job_scheduling", "copied": len(fields)}

def copy_labels(source_profile_id: int, target_profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    """Merge saved label definitions from another accessible profile by label name."""
    # Note: Label colors are configuration; torrent label assignments and runtime torrent state are intentionally not copied.
    actor_id = _actor_id(user_id)
    _validate_profiles(source_profile_id, target_profile_id, actor_id)
    now = utcnow()
    with connect() as conn:
        rows = conn.execute(
            "SELECT name,color FROM labels WHERE profile_id=? ORDER BY name COLLATE NOCASE, id",
            (int(source_profile_id),),
        ).fetchall()
        copied = 0
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            existing = conn.execute(
                "SELECT id FROM labels WHERE profile_id=? AND lower(name)=lower(?) ORDER BY id LIMIT 1",
                (int(target_profile_id), name),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE labels SET color=?, updated_at=? WHERE id=? AND profile_id=?",
                    (row.get("color") or "#64748b", now, int(existing["id"]), int(target_profile_id)),
                )
            else:
                conn.execute(
                    "INSERT INTO labels(user_id,profile_id,name,color,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (actor_id, int(target_profile_id), name, row.get("color") or "#64748b", now, now),
                )
            copied += 1
    return {"scope": "labels", "copied": copied}


def copy_download_planner(source_profile_id: int, target_profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    """Copy Download Planner configuration without copying runtime pauses or manual overrides."""
    # Note: Planner-paused torrent hashes, action history and a currently active manual override stay local to the destination profile.
    actor_id = _actor_id(user_id)
    _validate_profiles(source_profile_id, target_profile_id, actor_id)
    source = download_planner.get_settings(int(source_profile_id), actor_id)
    payload = {
        key: value for key, value in source.items()
        if key not in {"profile_id", "owner_user_id", "owner_name", "updated_at", "manual_override_until"}
    }
    copied = download_planner.save_settings(int(target_profile_id), payload, actor_id)
    return {"scope": "download_planner", "copied": len(payload), "settings": copied}


def copy_poller_settings(source_profile_id: int, target_profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    """Copy the normalized poller configuration into the active destination profile."""
    # Note: Only saved cadence/adaptive settings are copied; live counters, error state and poller runtime snapshots are never transferred.
    actor_id = _actor_id(user_id)
    _validate_profiles(source_profile_id, target_profile_id, actor_id)
    source = poller_control.get_settings(int(source_profile_id))
    copied = poller_control.save_settings(int(target_profile_id), dict(source))
    return {"scope": "poller", "copied": len(source), "settings": copied}


def copy_operation_log_settings(source_profile_id: int, target_profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    """Copy profile-level log retention policy without copying logs or retention execution metadata."""
    # Note: Last-run timestamps and deleted counters remain attached to the target history; only policy values are portable.
    actor_id = _actor_id(user_id)
    _validate_profiles(source_profile_id, target_profile_id, actor_id)
    source = operation_logs.get_settings(int(source_profile_id), actor_id)
    keys = (
        "retention_mode", "retention_days", "retention_lines", "retention_interval_hours",
        "job_retention_mode", "job_retention_days", "job_retention_lines", "job_retention_interval_hours",
        "operation_retention_mode", "operation_retention_days", "operation_retention_lines", "operation_retention_interval_hours",
    )
    payload = {key: source.get(key) for key in keys}
    copied = operation_logs.save_settings(int(target_profile_id), payload, actor_id)
    return {"scope": "operation_logs", "copied": len(payload), "settings": copied}

def copy_ratio_groups(source_profile_id: int, target_profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    """Merge ratio group definitions from another accessible profile by group name."""
    # Note: Target groups with the same profile-level name are updated in place; ratio history and torrent assignments are never copied.
    actor_id = _actor_id(user_id)
    _validate_profiles(source_profile_id, target_profile_id, actor_id)
    now = utcnow()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM ratio_groups WHERE profile_id=? ORDER BY id",
            (int(source_profile_id),),
        ).fetchall()
        copied = 0
        for row in rows:
            existing = conn.execute(
                "SELECT id FROM ratio_groups WHERE profile_id=? AND lower(name)=lower(?) ORDER BY id LIMIT 1",
                (int(target_profile_id), row.get("name") or ""),
            ).fetchone()
            values = (
                row.get("min_ratio"), row.get("max_ratio"), row.get("seed_time_minutes"), row.get("min_seed_time_minutes"),
                row.get("ignore_private"), row.get("ignore_active_upload"), row.get("active_upload_min_bytes"),
                row.get("move_path"), row.get("set_label"), row.get("action"), row.get("enabled"), now,
            )
            if existing:
                conn.execute(
                    "UPDATE ratio_groups SET min_ratio=?,max_ratio=?,seed_time_minutes=?,min_seed_time_minutes=?,ignore_private=?,ignore_active_upload=?,active_upload_min_bytes=?,move_path=?,set_label=?,action=?,enabled=?,updated_at=? WHERE id=?",
                    (*values, int(existing["id"])),
                )
            else:
                conn.execute(
                    "INSERT INTO ratio_groups(user_id,profile_id,name,min_ratio,max_ratio,seed_time_minutes,min_seed_time_minutes,ignore_private,ignore_active_upload,active_upload_min_bytes,move_path,set_label,action,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (actor_id, int(target_profile_id), row.get("name") or "Ratio group", *values[:-1], now, now),
                )
            copied += 1
    return {"scope": "ratio_groups", "copied": copied}


def _rss_row_signature(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    """Build a stable signature for RSS copy de-duplication."""
    # Note: Last-check timestamps and errors are intentionally excluded because only configuration is portable.
    return json.dumps({key: row.get(key) for key in keys}, sort_keys=True, separators=(",", ":"), default=str)


def copy_rss(source_profile_id: int, target_profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    """Merge RSS feed and rule configuration without copying match history or scheduler state."""
    # Note: Exact feed/rule duplicates are skipped so repeating the copy does not multiply background checks.
    actor_id = _actor_id(user_id)
    _validate_profiles(source_profile_id, target_profile_id, actor_id)
    feed_keys = ("name", "url", "enabled", "interval_minutes")
    rule_keys = (
        "name", "pattern", "exclude_pattern", "min_size_mb", "max_size_mb", "category", "quality",
        "season", "episode", "save_path", "label", "start", "enabled",
    )
    now = utcnow()
    with connect() as conn:
        source_feeds = conn.execute("SELECT * FROM rss_feeds WHERE profile_id=? ORDER BY id", (int(source_profile_id),)).fetchall()
        source_rules = conn.execute("SELECT * FROM rss_rules WHERE profile_id=? ORDER BY id", (int(source_profile_id),)).fetchall()
        target_feeds = conn.execute("SELECT * FROM rss_feeds WHERE profile_id=?", (int(target_profile_id),)).fetchall()
        target_rules = conn.execute("SELECT * FROM rss_rules WHERE profile_id=?", (int(target_profile_id),)).fetchall()
        existing_feeds = {_rss_row_signature(row, feed_keys) for row in target_feeds}
        existing_rules = {_rss_row_signature(row, rule_keys) for row in target_rules}
        copied_feeds = copied_rules = 0
        for row in source_feeds:
            signature = _rss_row_signature(row, feed_keys)
            if signature in existing_feeds:
                continue
            existing_feeds.add(signature)
            conn.execute(
                "INSERT INTO rss_feeds(profile_id,name,url,enabled,interval_minutes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (int(target_profile_id), row.get("name"), row.get("url"), row.get("enabled"), row.get("interval_minutes"), now, now),
            )
            copied_feeds += 1
        for row in source_rules:
            signature = _rss_row_signature(row, rule_keys)
            if signature in existing_rules:
                continue
            existing_rules.add(signature)
            conn.execute(
                "INSERT INTO rss_rules(profile_id,name,pattern,exclude_pattern,min_size_mb,max_size_mb,category,quality,season,episode,save_path,label,start,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(target_profile_id), *(row.get(key) for key in rule_keys), now, now),
            )
            copied_rules += 1
    return {"scope": "rss", "copied": copied_feeds + copied_rules, "feeds": copied_feeds, "rules": copied_rules}


def copy_rtorrent_config(source_profile_id: int, target_profile_id: int, user_id: int | None = None) -> dict[str, Any]:
    """Merge saved rTorrent overrides while taking fresh baseline values from the target daemon when available."""
    # Note: Source baseline values are never copied, target-only overrides keep their values/startup flags, and copied values are not applied immediately.
    actor_id = _actor_id(user_id)
    _, target = _validate_profiles(source_profile_id, target_profile_id, actor_id)
    overrides = rtorrent_config.saved_config_overrides(int(source_profile_id), actor_id)
    values = {key: item.get("value") for key, item in overrides.items() if item.get("value") is not None}
    if not values:
        return {"scope": "rtorrent_config", "copied": 0, "result": {"ok": True, "updated": [], "stored": [], "errors": []}}
    apply_on_start = any(bool(item.get("apply_on_start")) for item in overrides.values())
    with connect() as conn:
        target_only_flags = {
            str(row.get("key")): int(row.get("apply_on_start") or 0)
            for row in conn.execute(
                "SELECT key,apply_on_start FROM rtorrent_config_overrides WHERE profile_id=?",
                (int(target_profile_id),),
            ).fetchall()
            if str(row.get("key") or "") not in values
        }
    result = rtorrent_config.set_config(dict(target), values, apply_now=False, apply_on_start=apply_on_start)
    if target_only_flags:
        with connect() as conn:
            for key, startup_flag in target_only_flags.items():
                conn.execute(
                    "UPDATE rtorrent_config_overrides SET apply_on_start=? WHERE profile_id=? AND key=?",
                    (startup_flag, int(target_profile_id), key),
                )
    return {"scope": "rtorrent_config", "copied": len(result.get("stored") or []), "result": result}


def copy_profile_scope(source_profile_id: int, target_profile_id: int, scope: str, user_id: int | None = None) -> dict[str, Any]:
    """Dispatch a profile-copy request to the narrow service responsible for that configuration type."""
    # Note: One API surface keeps permission checks and source selection consistent across Preferences and automation tools.
    clean_scope = str(scope or "").strip().lower()
    if clean_scope.startswith("preferences."):
        return copy_preference_scope(source_profile_id, target_profile_id, clean_scope.split(".", 1)[1], user_id)
    handlers = {
        "profile_basics": copy_basic_profile_settings,
        "job_scheduling": copy_job_scheduling,
        "backup_schedule": copy_backup_schedule,
        "labels": copy_labels,
        "automations": copy_automations,
        "smart_queue": copy_smart_queue,
        "ratio_groups": copy_ratio_groups,
        "rss": copy_rss,
        "download_planner": copy_download_planner,
        "poller": copy_poller_settings,
        "operation_logs": copy_operation_log_settings,
        "rtorrent_config": copy_rtorrent_config,
    }
    handler = handlers.get(clean_scope)
    if not handler:
        raise ValueError("Unsupported profile copy scope")
    return handler(source_profile_id, target_profile_id, user_id)
