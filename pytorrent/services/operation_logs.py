from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from ..db import connect, utcnow, default_user_id
from . import auth

_socketio = None


def set_socketio(socketio) -> None:
    """Register Socket.IO so new operation logs can be streamed to the active profile room."""
    global _socketio
    _socketio = socketio


def _emit_created_log(profile_id: int, row: dict) -> None:
    """Emit one newly inserted log row without forcing clients to reload the whole table."""
    if not _socketio or not profile_id or not row:
        return
    _socketio.emit("operation_log_created", {"profile_id": int(profile_id), "log": row}, to=f"profile:{int(profile_id)}")

VALID_RETENTION_MODES = {"days", "lines", "both", "manual"}

DEFAULT_SETTINGS = {
    "retention_mode": "days",
    "retention_days": 30,
    "retention_lines": 5000,
    "retention_interval_hours": 24,
}
DEFAULT_CATEGORY_SETTINGS = {
    "job": {"retention_mode": "days", "retention_days": 7, "retention_lines": 2000, "retention_interval_hours": 24},
    "operation": {"retention_mode": "days", "retention_days": 30, "retention_lines": 5000, "retention_interval_hours": 24},
}
VALID_LOG_CATEGORIES = {"job", "operation"}
MAX_DETAIL_TEXT = 4000
MAX_DETAIL_ITEMS = 200


def _user_id(user_id: int | None = None) -> int:
    return int(user_id or auth.current_user_id() or default_user_id())


def _json_safe(value: Any, depth: int = 0) -> Any:
    """Convert operation details to JSON-safe data without dropping the whole payload on one bad value."""
    if depth > 8:
        return str(value)[:MAX_DETAIL_TEXT]
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > MAX_DETAIL_TEXT:
            return value[:MAX_DETAIL_TEXT] + "..."
        return value
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, (list, tuple, set)):
        data = list(value)
        safe = [_json_safe(item, depth + 1) for item in data[:MAX_DETAIL_ITEMS]]
        if len(data) > MAX_DETAIL_ITEMS:
            safe.append({"truncated_items": len(data) - MAX_DETAIL_ITEMS})
        return safe
    if isinstance(value, dict):
        items = list(value.items())
        safe = {str(k): _json_safe(v, depth + 1) for k, v in items[:MAX_DETAIL_ITEMS]}
        if len(items) > MAX_DETAIL_ITEMS:
            safe["truncated_keys"] = len(items) - MAX_DETAIL_ITEMS
        return safe
    return str(value)[:MAX_DETAIL_TEXT]


def _details(value: dict | None = None) -> str:
    """Serialize details defensively so partial non-serializable values do not erase the log details."""
    try:
        return json.dumps(_json_safe(value or {}), ensure_ascii=False, sort_keys=True)
    except Exception as exc:
        return json.dumps({"serialization_error": str(exc), "raw_type": type(value).__name__}, ensure_ascii=False)


def _compact_detail_value(value: Any) -> str:
    """Build a readable one-line value for the Details column while keeping full JSON separately."""
    if value in (None, ""):
        return ""
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        return f"{len(value)} item(s)"
    if isinstance(value, dict):
        if not value:
            return ""
        return f"{len(value)} field(s)"
    text = str(value)
    return text if len(text) <= 160 else text[:157] + "..."


def _details_summary(details: dict) -> str:
    """Summarize important detail fields without hiding the full details_json payload."""
    priority = [
        "status", "job_id", "attempt", "attempts", "count", "hash_count", "action",
        "source", "source_label", "directory", "label", "target_path", "remove_data",
        "move_data", "target_profile_id", "move_data_downgraded", "keep_seeding", "error", "error_count", "result_count",
    ]
    parts: list[str] = []
    for key in priority:
        if key in details:
            value = _compact_detail_value(details.get(key))
            if value:
                parts.append(f"{key}: {value}")
    for key, raw in details.items():
        if key in priority:
            continue
        value = _compact_detail_value(raw)
        if value:
            parts.append(f"{key}: {value}")
        if len(parts) >= 10:
            break
    return ", ".join(parts)


_TRANSFER_PRIVATE_DETAIL_KEYS = {
    "target_profile_id",
    "target_path",
    "target_allowed_roots",
    "moved_to",
    "load_directory",
}


def _find_transfer_target_profile_id(value: Any) -> int:
    """Find a destination profile identifier inside nested transfer log details."""
    # Note: Job details can place target_profile_id in a nested context or result, so authorization cannot inspect only the top level.
    if isinstance(value, dict):
        if "target_profile_id" in value:
            try:
                return int(value.get("target_profile_id") or 0)
            except (TypeError, ValueError):
                pass
        for item in value.values():
            target_id = _find_transfer_target_profile_id(item)
            if target_id:
                return target_id
    elif isinstance(value, list):
        for item in value:
            target_id = _find_transfer_target_profile_id(item)
            if target_id:
                return target_id
    return 0


def _scrub_transfer_private_details(value: Any) -> Any:
    """Remove destination-only identifiers and paths from nested transfer log details."""
    # Note: Source-only readers must not recover target profile paths from nested result or item objects.
    if isinstance(value, dict):
        return {
            key: _scrub_transfer_private_details(item)
            for key, item in value.items()
            if key not in _TRANSFER_PRIVATE_DETAIL_KEYS
        }
    if isinstance(value, list):
        return [_scrub_transfer_private_details(item) for item in value]
    return value


def _row_to_public(row: dict, *, viewer_user_id: int | None = None, conservative_transfer: bool = False) -> dict:
    """Decorate one log row with parsed details and explicit execution identity."""
    # Note: System identity and destination-profile redaction are resolved by the backend before log details reach clients.
    item = dict(row)
    if str(item.get("actor_type") or "user") == "system":
        item["executed_by_user_id"] = None
        item["actor_label"] = "System"
    else:
        item["executed_by_user_id"] = item.get("user_id")
        item["actor_label"] = "User"
    try:
        item["details"] = json.loads(item.get("details_json") or "{}")
    except Exception:
        item["details"] = {}
    if str(item.get("action") or "") == "profile_transfer":
        target_id = _find_transfer_target_profile_id(item["details"])
        can_read_target = False
        if target_id and not conservative_transfer:
            try:
                can_read_target = auth.can_access_profile(target_id, viewer_user_id)
            except Exception:
                can_read_target = False
        if target_id and not can_read_target:
            item["details"] = _scrub_transfer_private_details(item["details"])
            item["details"]["target_profile_hidden"] = True
            item["details_json"] = json.dumps(item["details"], ensure_ascii=False, sort_keys=True)
    item["details_h"] = _details_summary(item["details"])
    return item


def _sanitize_mode(value: Any, default: str = "days") -> str:
    mode = str(value or default).lower()
    return mode if mode in VALID_RETENTION_MODES else default


def _sanitize_days(value: Any, default: int) -> int:
    return max(1, min(3650, int(value or default)))


def _sanitize_lines(value: Any, default: int) -> int:
    return max(100, min(1_000_000, int(value or default)))


def _sanitize_interval(value: Any, default: int = 24) -> int:
    return max(1, min(8760, int(value or default)))


def _log_category(event_type: str = "", source: str = "") -> str:
    return "job" if str(source or "") in {"job", "worker"} or str(event_type or "").startswith("job_") else "operation"


def _category_where(category: str) -> str:
    if category == "job":
        return "(COALESCE(source, '') IN ('job', 'worker') OR event_type LIKE 'job_%')"
    return "NOT (COALESCE(source, '') IN ('job', 'worker') OR event_type LIKE 'job_%')"


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _next_retention_run(settings: dict, category: str) -> str | None:
    last = _parse_dt(settings.get(f"{category}_last_retention_run_at"))
    if not last:
        return None
    return (last + timedelta(hours=int(settings.get(f"{category}_retention_interval_hours") or 24))).isoformat(timespec="seconds")


def get_settings(profile_id: int = 0, user_id: int | None = None) -> dict:
    """Return one shared retention configuration for the profile."""
    profile_id = int(profile_id or 0)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM operation_log_settings WHERE profile_id=?",
            (profile_id,),
        ).fetchone()
    if not row:
        data = {"updated_by_user_id": 0, "profile_id": profile_id, **DEFAULT_SETTINGS}
    else:
        data = {**DEFAULT_SETTINGS, **dict(row)}
    data["profile_id"] = profile_id
    data["updated_by_user_id"] = int(data.get("updated_by_user_id") or 0)
    # Backward-compatible alias for older clients. The value is audit metadata, not ownership.
    data["owner_user_id"] = data["updated_by_user_id"]
    data["retention_mode"] = _sanitize_mode(data.get("retention_mode"), DEFAULT_SETTINGS["retention_mode"])
    data["retention_days"] = _sanitize_days(data.get("retention_days"), DEFAULT_SETTINGS["retention_days"])
    data["retention_lines"] = _sanitize_lines(data.get("retention_lines"), DEFAULT_SETTINGS["retention_lines"])
    data["retention_interval_hours"] = _sanitize_interval(data.get("retention_interval_hours"), DEFAULT_SETTINGS["retention_interval_hours"])
    for category, defaults in DEFAULT_CATEGORY_SETTINGS.items():
        data[f"{category}_retention_mode"] = _sanitize_mode(data.get(f"{category}_retention_mode") or data.get("retention_mode"), defaults["retention_mode"])
        data[f"{category}_retention_days"] = _sanitize_days(data.get(f"{category}_retention_days") or data.get("retention_days"), defaults["retention_days"])
        data[f"{category}_retention_lines"] = _sanitize_lines(data.get(f"{category}_retention_lines") or data.get("retention_lines"), defaults["retention_lines"])
        data[f"{category}_retention_interval_hours"] = _sanitize_interval(data.get(f"{category}_retention_interval_hours") or data.get("retention_interval_hours"), defaults["retention_interval_hours"])
        data[f"{category}_last_retention_deleted"] = max(0, int(data.get(f"{category}_last_retention_deleted") or 0))
        data[f"{category}_next_retention_run_at"] = _next_retention_run(data, category)
    return data


def save_settings(profile_id: int, data: dict, user_id: int | None = None) -> dict:
    user_id = _user_id(user_id)
    profile_id = int(profile_id or 0)
    now = utcnow()
    if not auth.can_write_profile(profile_id, user_id):
        raise PermissionError("No write access to profile")
    current = get_settings(profile_id, user_id)
    legacy_mode = _sanitize_mode(data.get("retention_mode") or current.get("retention_mode"), DEFAULT_SETTINGS["retention_mode"])
    legacy_days = _sanitize_days(data.get("retention_days") or current.get("retention_days"), DEFAULT_SETTINGS["retention_days"])
    legacy_lines = _sanitize_lines(data.get("retention_lines") or current.get("retention_lines"), DEFAULT_SETTINGS["retention_lines"])
    legacy_interval = _sanitize_interval(data.get("retention_interval_hours") or current.get("retention_interval_hours"), DEFAULT_SETTINGS["retention_interval_hours"])
    values: dict[str, Any] = {
        "retention_mode": legacy_mode,
        "retention_days": legacy_days,
        "retention_lines": legacy_lines,
        "retention_interval_hours": legacy_interval,
    }
    for category, defaults in DEFAULT_CATEGORY_SETTINGS.items():
        values[f"{category}_retention_mode"] = _sanitize_mode(data.get(f"{category}_retention_mode") or current.get(f"{category}_retention_mode"), defaults["retention_mode"])
        values[f"{category}_retention_days"] = _sanitize_days(data.get(f"{category}_retention_days") or current.get(f"{category}_retention_days"), defaults["retention_days"])
        values[f"{category}_retention_lines"] = _sanitize_lines(data.get(f"{category}_retention_lines") or current.get(f"{category}_retention_lines"), defaults["retention_lines"])
        values[f"{category}_retention_interval_hours"] = _sanitize_interval(data.get(f"{category}_retention_interval_hours") or current.get(f"{category}_retention_interval_hours"), defaults["retention_interval_hours"])
        values[f"{category}_last_retention_run_at"] = current.get(f"{category}_last_retention_run_at")
        values[f"{category}_last_retention_deleted"] = int(current.get(f"{category}_last_retention_deleted") or 0)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO operation_log_settings(
              profile_id, updated_by_user_id, retention_mode, retention_days, retention_lines,
              retention_interval_hours,
              job_retention_mode, job_retention_days, job_retention_lines, job_retention_interval_hours, job_last_retention_run_at, job_last_retention_deleted,
              operation_retention_mode, operation_retention_days, operation_retention_lines, operation_retention_interval_hours, operation_last_retention_run_at, operation_last_retention_deleted,
              created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(profile_id) DO UPDATE SET
              updated_by_user_id=excluded.updated_by_user_id,
              retention_mode=excluded.retention_mode,
              retention_days=excluded.retention_days,
              retention_lines=excluded.retention_lines,
              retention_interval_hours=excluded.retention_interval_hours,
              job_retention_mode=excluded.job_retention_mode,
              job_retention_days=excluded.job_retention_days,
              job_retention_lines=excluded.job_retention_lines,
              job_retention_interval_hours=excluded.job_retention_interval_hours,
              job_last_retention_run_at=excluded.job_last_retention_run_at,
              job_last_retention_deleted=excluded.job_last_retention_deleted,
              operation_retention_mode=excluded.operation_retention_mode,
              operation_retention_days=excluded.operation_retention_days,
              operation_retention_lines=excluded.operation_retention_lines,
              operation_retention_interval_hours=excluded.operation_retention_interval_hours,
              operation_last_retention_run_at=excluded.operation_last_retention_run_at,
              operation_last_retention_deleted=excluded.operation_last_retention_deleted,
              updated_at=excluded.updated_at
            """,
            (
                profile_id, user_id, values["retention_mode"], values["retention_days"], values["retention_lines"], values["retention_interval_hours"],
                values["job_retention_mode"], values["job_retention_days"], values["job_retention_lines"], values["job_retention_interval_hours"], values["job_last_retention_run_at"], values["job_last_retention_deleted"],
                values["operation_retention_mode"], values["operation_retention_days"], values["operation_retention_lines"], values["operation_retention_interval_hours"], values["operation_last_retention_run_at"], values["operation_last_retention_deleted"],
                now, now,
            ),
        )
    return get_settings(profile_id, user_id)


def record(profile_id: int | None, event_type: str, message: str, *, severity: str = "info", source: str = "system", torrent_hash: str | None = None, torrent_name: str | None = None, action: str | None = None, details: dict | None = None, user_id: int | None = None, actor_type: str = "user") -> int:
    """Insert one operation log row and lazily run retention for its category when due."""
    # Note: actor_type separates scheduler/system execution from the fallback user id required by legacy database rows.
    now = utcnow()
    user_id = _user_id(user_id)
    actor_type = "system" if str(actor_type or "").lower() == "system" else "user"
    event_type_s = str(event_type)
    source_s = str(source or "system")
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO operation_logs(user_id, profile_id, event_type, severity, source, torrent_hash, torrent_name, action, message, details_json, actor_type, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (user_id, int(profile_id or 0) or None, event_type_s, str(severity or "info"), source_s, torrent_hash, torrent_name, action, str(message), _details(details), actor_type, now),
        )
        row_id = int(cur.lastrowid)
    try:
        maybe_apply_retention(int(profile_id or 0), _log_category(event_type_s, source_s), user_id=user_id)
    except Exception:
        # Logging must never fail because cleanup metadata could not be updated.
        pass
    if int(profile_id or 0):
        try:
            # Note: Fetch after retention so a row removed immediately by policy is never streamed to the browser.
            with connect() as conn:
                created = conn.execute("SELECT * FROM operation_logs WHERE id=?", (row_id,)).fetchone()
            if created:
                # Note: One room can contain source-only readers, so live transfer logs use the conservative redacted representation.
                _emit_created_log(int(profile_id), _row_to_public(created, conservative_transfer=True))
        except Exception:
            # Note: Live delivery is best-effort and must never make log persistence fail.
            pass
    return row_id


def _job_event_type(status: str) -> str:
    """Map worker states to explicit operation log event types without changing old done/failed names."""
    return {
        "queued": "job_queued",
        "started": "job_started",
        "done": "job_done",
        "failed": "job_failed",
        "retry": "job_retry",
        "cancelled": "job_cancelled",
        "timeout": "job_timeout",
        "resubmitted": "job_resubmitted",
        "forced": "job_forced",
    }.get(str(status), "job_event")


def _job_severity(status: str) -> str:
    """Use severity consistently for filtering and badge rendering."""
    if status in {"failed", "timeout"}:
        return "danger"
    if status in {"retry", "resubmitted", "cancelled", "forced"}:
        return "warning"
    return "info"


def _job_action_label(action: str) -> str:
    """Return a stable human-readable action label for log messages."""
    labels = {
        "add_magnet": "Magnet link",
        "add_torrent_raw": "Torrent file",
        "set_label": "Set label",
        "set_ratio_group": "Set ratio group",
        "set_limits": "Set speed limits",
        "smart_queue_check": "Smart Queue check",
        "profile_transfer": "Move to another profile",
    }
    return labels.get(str(action or ""), str(action or "job"))


def _result_summary(result: dict) -> dict:
    """Extract compact result counters while preserving full result in details."""
    result = result or {}
    results = result.get("results") if isinstance(result.get("results"), list) else []
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    ignored_errors = result.get("ignored_errors") if isinstance(result.get("ignored_errors"), list) else []
    skipped = [item for item in results if isinstance(item, dict) and item.get("skipped")]
    return {
        "result_count": len(results) if results is not None else result.get("count"),
        "error_count": len(errors or []) + len(ignored_errors or []),
        "skipped_count": len(skipped),
    }


def _result_items_by_hash(result: dict) -> dict[str, dict]:
    """Index per-torrent action results so job log details show skipped broken hashes inline."""
    rows = result.get("results") if isinstance((result or {}).get("results"), list) else []
    return {str(item.get("hash")): item for item in rows if isinstance(item, dict) and item.get("hash")}


def record_job_event(profile_id: int, action: str, status: str, payload: dict | None, result: dict | None = None, error: str = "", job_id: str | None = None, user_id: int | None = None, actor_type: str = "user") -> None:
    """Record queued, running and terminal job states with per-torrent context when available."""
    payload = payload or {}
    result = result or {}
    hashes = payload.get("hashes") or []
    ctx = payload.get("job_context") or {}
    items = ctx.get("items") or []
    by_hash = {str(item.get("hash")): item for item in items if item}
    result_by_hash = _result_items_by_hash(result)
    event_type = _job_event_type(str(status))
    severity = _job_severity(str(status))
    context_source = str(ctx.get("source") or payload.get("source") or "user")
    source_label = str(ctx.get("rule_name") or ctx.get("source") or context_source)
    source = "job"
    base_details = {
        "job_id": job_id,
        "status": status,
        "source": context_source,
        "source_label": source_label,
        "directory": payload.get("directory"),
        "label": payload.get("label"),
        "target_path": ctx.get("target_path") or payload.get("path"),
        "remove_data": ctx.get("remove_data") or payload.get("remove_data"),
        "move_data": ctx.get("move_data") or payload.get("move_data"),
        "target_profile_id": ctx.get("target_profile_id") or payload.get("target_profile_id"),
        "move_data_downgraded": ctx.get("move_data_downgraded") or payload.get("move_data_downgraded"),
        "keep_seeding": payload.get("keep_seeding"),
        "hash_count": len(hashes),
        "error": error,
        "result": result,
        **_result_summary(result),
    }
    if action in {"add_magnet", "add_torrent_raw"}:
        name = str(payload.get("name") or payload.get("filename") or payload.get("uri") or "torrent")[:300]
        status_label = {"queued": "queued", "started": "started", "done": "added", "failed": "failed", "retry": "retry scheduled", "cancelled": "cancelled"}.get(str(status), str(status))
        msg = f"{_job_action_label(action)} {status_label}: {name}"
        record(profile_id, "torrent_added" if status == "done" else event_type, msg, severity=severity, source=source, action=action, details=base_details, user_id=user_id, actor_type=actor_type)
        return
    if not hashes:
        record(profile_id, event_type, f"{_job_action_label(action)} {status}", severity=severity, source=source, action=action, details=base_details, user_id=user_id, actor_type=actor_type)
        return
    for h in hashes:
        item = by_hash.get(str(h)) or {}
        result_item = result_by_hash.get(str(h)) or {}
        name = str(result_item.get("name") or item.get("name") or h)
        row_details = {**base_details, "item": item, "result_item": result_item}
        if result_item.get("skipped"):
            row_details["skipped"] = result_item.get("skipped")
            row_details["skipped_reason"] = result_item.get("skipped_reason")
        record(profile_id, "torrent_removed" if action == "remove" and status == "done" else event_type, f"{_job_action_label(action)} {status}: {name}", severity=severity, source=source, torrent_hash=str(h), torrent_name=name, action=action, details=row_details, user_id=user_id, actor_type=actor_type)


def record_worker_event(profile_id: int, action: str, status: str, message: str, *, payload: dict | None = None, job_id: str | None = None, user_id: int | None = None, error: str = "", details: dict | None = None, actor_type: str = "user") -> None:
    """Log worker-only lifecycle events that do not execute the normal job action path."""
    payload = payload or {}
    merged = {"job_id": job_id, "status": status, "error": error, "payload": payload, **(details or {})}
    record(profile_id, _job_event_type(status), message, severity=_job_severity(status), source="worker", action=action, details=merged, user_id=user_id, actor_type=actor_type)


def _torrent_complete(row: dict | None) -> bool:
    """Return completion state from either full-list rows or lightweight live-stat patches."""
    # Note: Low CPU mode often updates only live fields, so completion must be detected from partial patches too.
    row = row or {}
    try:
        progress = float(row.get("progress") or 0)
    except Exception:
        progress = 0.0
    return bool(row.get("complete")) or progress >= 100


def _torrent_detail(old: dict, patch: dict | None = None) -> dict:
    """Build stable details for torrent operation logs without requiring a full refreshed row."""
    # Note: Prefer fresh values from the patch, but fall back to cached full-row metadata for readable logs.
    patch = patch or {}
    return {
        "ratio": patch.get("ratio", old.get("ratio")),
        "size": patch.get("size", old.get("size")),
        "path": patch.get("path", old.get("path")),
        "label": patch.get("label", old.get("label")),
        "tracker": patch.get("tracker", old.get("tracker")),
    }


def record_cache_diff(profile_id: int, added: list[dict], removed: list[str], updated: list[dict], old_rows: dict[str, dict]) -> None:
    """Record torrent cache changes detected by the poller without depending on manual jobs."""
    # Note: This covers full-list polling; live-only completion patches are handled by the same transition logic below.
    for row in added or []:
        record(profile_id, "torrent_added", f"Torrent added: {row.get('name') or row.get('hash')}", source="poller", actor_type="system", torrent_hash=row.get("hash"), torrent_name=row.get("name"), details={"size": row.get("size"), "path": row.get("path"), "label": row.get("label"), "tracker": row.get("tracker")})
    for h in removed or []:
        old = old_rows.get(str(h)) or {}
        record(profile_id, "torrent_removed", f"Torrent removed: {old.get('name') or h}", source="poller", actor_type="system", torrent_hash=str(h), torrent_name=old.get("name"), details={"path": old.get("path"), "label": old.get("label"), "tracker": old.get("tracker")})
    for patch in updated or []:
        h = str(patch.get("hash") or "")
        old = old_rows.get(h) or {}
        merged = {**old, **patch}
        was_complete = _torrent_complete(old)
        is_complete = _torrent_complete(merged)
        if h and not was_complete and is_complete:
            record(profile_id, "torrent_completed", f"Torrent completed: {old.get('name') or patch.get('name') or h}", source="poller", actor_type="system", torrent_hash=h, torrent_name=old.get("name") or patch.get("name"), details=_torrent_detail(old, patch))


def list_logs(profile_id: int, *, limit: int = 200, offset: int = 0, event_type: str = "", q: str = "", hide_jobs: bool = False, hide_automations: bool = False) -> dict:
    """Return operation logs with searchable messages, torrents, actions and detail JSON."""
    limit = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))
    where = ["profile_id=?"]
    params: list[Any] = [int(profile_id or 0)]
    if event_type:
        where.append("event_type=?")
        params.append(event_type)
    if hide_jobs:
        where.append("COALESCE(source, '') NOT IN ('job', 'worker') AND event_type NOT LIKE 'job_%'")
    if hide_automations:
        # Note: Keeps automatic background automation entries out of the default log view without deleting them.
        where.append("COALESCE(action, '') != 'background_automation' AND event_type NOT LIKE 'background_automation_%' AND COALESCE(details_json, '') NOT LIKE '%\"source\": \"automation\"%'")
    if q:
        where.append("(message LIKE ? OR torrent_name LIKE ? OR torrent_hash LIKE ? OR action LIKE ? OR details_json LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like, like, like])
    sql_where = " WHERE " + " AND ".join(where)
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM operation_logs{sql_where} ORDER BY id DESC LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        total = conn.execute(f"SELECT COUNT(*) AS n FROM operation_logs{sql_where}", tuple(params)).fetchone()["n"]
    viewer_user_id = auth.current_user_id()
    return {"logs": [_row_to_public(r, viewer_user_id=viewer_user_id) for r in rows], "total": int(total or 0), "limit": limit, "offset": offset}


def stats(profile_id: int) -> dict:
    profile_id = int(profile_id or 0)
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM operation_logs WHERE profile_id=?", (profile_id,)).fetchone()["n"]
        by_type = conn.execute("SELECT event_type, COUNT(*) AS n FROM operation_logs WHERE profile_id=? GROUP BY event_type ORDER BY n DESC LIMIT 12", (profile_id,)).fetchall()
        by_day = conn.execute("SELECT substr(created_at,1,10) AS bucket, COUNT(*) AS n FROM operation_logs WHERE profile_id=? GROUP BY bucket ORDER BY bucket DESC LIMIT 14", (profile_id,)).fetchall()
        by_month = conn.execute("SELECT substr(created_at,1,7) AS bucket, COUNT(*) AS n FROM operation_logs WHERE profile_id=? GROUP BY bucket ORDER BY bucket DESC LIMIT 12", (profile_id,)).fetchall()
        top_actions = conn.execute("SELECT COALESCE(action, event_type) AS action, COUNT(*) AS n FROM operation_logs WHERE profile_id=? GROUP BY COALESCE(action, event_type) ORDER BY n DESC LIMIT 12", (profile_id,)).fetchall()
    return {"total": int(total or 0), "by_type": by_type, "by_day": by_day, "by_month": by_month, "top_actions": top_actions, "settings": get_settings(profile_id)}


def _retention_label_for(settings: dict, category: str) -> str:
    mode = settings.get(f"{category}_retention_mode") or "days"
    days = settings.get(f"{category}_retention_days") or DEFAULT_CATEGORY_SETTINGS[category]["retention_days"]
    lines = settings.get(f"{category}_retention_lines") or DEFAULT_CATEGORY_SETTINGS[category]["retention_lines"]
    interval = settings.get(f"{category}_retention_interval_hours") or DEFAULT_CATEGORY_SETTINGS[category]["retention_interval_hours"]
    if mode == "manual":
        return f"manual cleanup only, checked every {interval}h"
    if mode == "lines":
        return f"retention {lines} lines, checked every {interval}h"
    if mode == "both":
        return f"retention {days} days and {lines} lines, checked every {interval}h"
    return f"retention {days} days, checked every {interval}h"


def retention_label(settings: dict) -> str:
    return f"Jobs: {_retention_label_for(settings, 'job')} / Operations: {_retention_label_for(settings, 'operation')}"


def clear(profile_id: int, *, event_type: str = "", category: str = "") -> int:
    where = ["profile_id=?"]
    params: list[Any] = [int(profile_id or 0)]
    if category in VALID_LOG_CATEGORIES:
        where.append(_category_where(category))
    if event_type:
        where.append("event_type=?")
        params.append(event_type)
    with connect() as conn:
        cur = conn.execute("DELETE FROM operation_logs WHERE " + " AND ".join(where), tuple(params))
        return int(cur.rowcount or 0)


def _apply_retention_category(conn, profile_id: int, settings: dict, category: str) -> dict:
    mode = settings.get(f"{category}_retention_mode") or "manual"
    deleted_days = 0
    deleted_lines = 0
    base_where = f"profile_id=? AND {_category_where(category)}"
    if mode in {"days", "both"}:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(settings[f"{category}_retention_days"]))).isoformat(timespec="seconds")
        cur = conn.execute(f"DELETE FROM operation_logs WHERE {base_where} AND created_at<?", (int(profile_id or 0), cutoff))
        deleted_days = int(cur.rowcount or 0)
    if mode in {"lines", "both"}:
        keep = int(settings[f"{category}_retention_lines"])
        cur = conn.execute(
            f"""
            DELETE FROM operation_logs
            WHERE id IN (
              SELECT id FROM operation_logs
              WHERE {base_where}
              ORDER BY id DESC
              LIMIT -1 OFFSET ?
            )
            """,
            (int(profile_id or 0), keep),
        )
        deleted_lines = int(cur.rowcount or 0)
    return {"deleted_days": deleted_days, "deleted_lines": deleted_lines, "deleted": deleted_days + deleted_lines}


def _update_retention_metadata(conn, profile_id: int, category: str, deleted: int, settings: dict, user_id: int | None = None) -> None:
    """Update retention runtime metadata without changing the last-editor audit field."""
    now = utcnow()
    profile_id = int(profile_id or 0)
    cur = conn.execute(
        f"""
        UPDATE operation_log_settings
        SET {category}_last_retention_run_at=?, {category}_last_retention_deleted=?, updated_at=?
        WHERE profile_id=?
        """,
        (now, int(deleted or 0), now, profile_id),
    )
    if int(cur.rowcount or 0) == 0:
        values = {
            "retention_mode": _sanitize_mode(settings.get("retention_mode"), DEFAULT_SETTINGS["retention_mode"]),
            "retention_days": _sanitize_days(settings.get("retention_days"), DEFAULT_SETTINGS["retention_days"]),
            "retention_lines": _sanitize_lines(settings.get("retention_lines"), DEFAULT_SETTINGS["retention_lines"]),
            "retention_interval_hours": _sanitize_interval(settings.get("retention_interval_hours"), DEFAULT_SETTINGS["retention_interval_hours"]),
        }
        for cat, defaults in DEFAULT_CATEGORY_SETTINGS.items():
            values[f"{cat}_retention_mode"] = _sanitize_mode(settings.get(f"{cat}_retention_mode"), defaults["retention_mode"])
            values[f"{cat}_retention_days"] = _sanitize_days(settings.get(f"{cat}_retention_days"), defaults["retention_days"])
            values[f"{cat}_retention_lines"] = _sanitize_lines(settings.get(f"{cat}_retention_lines"), defaults["retention_lines"])
            values[f"{cat}_retention_interval_hours"] = _sanitize_interval(settings.get(f"{cat}_retention_interval_hours"), defaults["retention_interval_hours"])
            values[f"{cat}_last_retention_run_at"] = settings.get(f"{cat}_last_retention_run_at")
            values[f"{cat}_last_retention_deleted"] = int(settings.get(f"{cat}_last_retention_deleted") or 0)
        values[f"{category}_last_retention_run_at"] = now
        values[f"{category}_last_retention_deleted"] = int(deleted or 0)
        audit_user_id = int(user_id or 0) or None
        conn.execute(
            """
            INSERT INTO operation_log_settings(
              profile_id, updated_by_user_id, retention_mode, retention_days, retention_lines,
              retention_interval_hours,
              job_retention_mode, job_retention_days, job_retention_lines, job_retention_interval_hours, job_last_retention_run_at, job_last_retention_deleted,
              operation_retention_mode, operation_retention_days, operation_retention_lines, operation_retention_interval_hours, operation_last_retention_run_at, operation_last_retention_deleted,
              created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(profile_id) DO UPDATE SET
              job_last_retention_run_at=excluded.job_last_retention_run_at,
              job_last_retention_deleted=excluded.job_last_retention_deleted,
              operation_last_retention_run_at=excluded.operation_last_retention_run_at,
              operation_last_retention_deleted=excluded.operation_last_retention_deleted,
              updated_at=excluded.updated_at
            """,
            (
                profile_id, audit_user_id, values["retention_mode"], values["retention_days"], values["retention_lines"], values["retention_interval_hours"],
                values["job_retention_mode"], values["job_retention_days"], values["job_retention_lines"], values["job_retention_interval_hours"], values["job_last_retention_run_at"], values["job_last_retention_deleted"],
                values["operation_retention_mode"], values["operation_retention_days"], values["operation_retention_lines"], values["operation_retention_interval_hours"], values["operation_last_retention_run_at"], values["operation_last_retention_deleted"],
                now, now,
            ),
        )


def apply_retention(profile_id: int, user_id: int | None = None, category: str = "all") -> dict:
    """Apply due operation-log retention without touching torrent data or other history tables."""
    profile_id = int(profile_id or 0)
    settings = get_settings(profile_id, user_id)
    categories = [category] if category in VALID_LOG_CATEGORIES else ["job", "operation"]
    results: dict[str, Any] = {}
    total = 0
    with connect() as conn:
        for cat in categories:
            item = _apply_retention_category(conn, profile_id, settings, cat)
            _update_retention_metadata(conn, profile_id, cat, int(item["deleted"]), settings, user_id=user_id)
            results[cat] = item
            total += int(item["deleted"])
    fresh = get_settings(profile_id, user_id)
    return {"deleted": total, "categories": results, "settings": fresh}


def maybe_apply_retention(profile_id: int, category: str, user_id: int | None = None) -> dict:
    """Run retention for a category only when interval since last cleanup elapsed."""
    if category not in VALID_LOG_CATEGORIES:
        category = "operation"
    settings = get_settings(profile_id, user_id)
    interval = int(settings.get(f"{category}_retention_interval_hours") or 24)
    last = _parse_dt(settings.get(f"{category}_last_retention_run_at"))
    now = datetime.now(timezone.utc)
    if last and now < last + timedelta(hours=interval):
        return {"skipped": True, "category": category, "next_run_at": (last + timedelta(hours=interval)).isoformat(timespec="seconds"), "settings": settings}
    result = apply_retention(profile_id, user_id=user_id, category=category)
    result["skipped"] = False
    result["category"] = category
    return result
