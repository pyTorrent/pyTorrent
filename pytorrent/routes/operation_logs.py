from __future__ import annotations
from ._shared import *
from ..services import operation_logs


def _active_profile_or_400():
    profile = request_profile()
    if not profile:
        return None
    return profile


@bp.get("/operation-logs")
def operation_logs_list():
    profile = _active_profile_or_400()
    if not profile:
        return ok({"logs": [], "total": 0, "stats": {}, "settings": operation_logs.get_settings(0), "error": "No profile"})
    data = operation_logs.list_logs(
        int(profile["id"]),
        limit=int(request.args.get("limit") or 200),
        offset=int(request.args.get("offset") or 0),
        event_type=str(request.args.get("type") or "").strip(),
        q=str(request.args.get("q") or "").strip(),
        hide_jobs=str(request.args.get("hide_jobs") or "").lower() in {"1", "true", "yes", "on"},
        hide_automations=str(request.args.get("hide_automations") or "").lower() in {"1", "true", "yes", "on"},
    )
    data["settings"] = operation_logs.get_settings(int(profile["id"]))
    if str(request.args.get("stats") or "").lower() in {"1", "true", "yes", "on"}:
        data["stats"] = operation_logs.stats(int(profile["id"]))
        data["settings"] = data["stats"].get("settings", data["settings"])
    return ok(data)


@bp.get("/operation-logs/stats")
def operation_logs_stats():
    profile = _active_profile_or_400()
    if not profile:
        return ok({"stats": {}, "settings": operation_logs.get_settings(0), "error": "No profile"})
    stats = operation_logs.stats(int(profile["id"]))
    return ok({"stats": stats, "settings": stats.get("settings")})


@bp.post("/operation-logs/settings")
def operation_logs_settings_save():
    profile = _active_profile_or_400()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    try:
        settings = operation_logs.save_settings(int(profile["id"]), request.get_json(silent=True) or {})
        return ok({"settings": settings})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403 if isinstance(exc, PermissionError) else 400


@bp.post("/operation-logs/clear")
def operation_logs_clear():
    profile = _active_profile_or_400()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    event_type = str((request.get_json(silent=True) or {}).get("event_type") or "").strip()
    return ok({"deleted": operation_logs.clear(int(profile["id"]), event_type=event_type)})


@bp.post("/operation-logs/apply-retention")
def operation_logs_apply_retention():
    profile = _active_profile_or_400()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    category = str((request.get_json(silent=True) or {}).get("category") or "all").strip().lower()
    return ok(operation_logs.apply_retention(int(profile["id"]), category=category))
