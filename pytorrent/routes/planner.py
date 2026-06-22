from __future__ import annotations

from flask import jsonify, request

from ._shared import bp, request_profile
from ..services import download_planner, poller_control
from ..services.auth import current_user_id

def ok(payload=None):
    data = {"ok": True}
    if payload:
        data.update(payload)
    return jsonify(data)


def _profile_or_error():
    profile = request_profile()
    if not profile:
        return None, (jsonify({"ok": False, "error": "No profile"}), 400)
    return profile, None


@bp.get("/download-planner")
def download_planner_get():
    profile, error = _profile_or_error()
    if error:
        return error
    return ok({"settings": download_planner.get_settings(int(profile["id"]), current_user_id())})


@bp.post("/download-planner")
def download_planner_save():
    # Note: Planner settings are saved through one canonical endpoint to keep the frontend/backend contract explicit.
    profile, error = _profile_or_error()
    if error:
        return error
    try:
        settings = download_planner.save_settings(int(profile["id"]), request.get_json(silent=True) or {}, current_user_id())
        return ok({"settings": settings})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/download-planner/check")
def download_planner_check():
    profile, error = _profile_or_error()
    if error:
        return error
    try:
        data = request.get_json(silent=True) or {}
        run_profile = dict(profile)
        if data.get("dry_run"):
            run_profile["dry_run"] = "true"
        return ok({"result": download_planner.enforce(run_profile, force=True)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.get("/download-planner/preview")
def download_planner_preview():
    profile, error = _profile_or_error()
    if error:
        return error
    return ok({"preview": download_planner.preview(profile), "history": download_planner.history(int(profile["id"]), int(request.args.get("history_limit") or 40)), "history_total": download_planner.history_count(int(profile["id"]))})


@bp.delete("/download-planner/history")
def download_planner_history_clear():
    profile, error = _profile_or_error()
    if error:
        return error
    try:
        deleted = download_planner.clear_history(int(profile["id"]))
        return ok({"deleted": deleted, "history": [], "history_total": 0})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/download-planner/override")
def download_planner_override():
    profile, error = _profile_or_error()
    if error:
        return error
    try:
        seconds = int((request.get_json(silent=True) or {}).get("seconds") or 0)
        return ok(download_planner.set_manual_override(int(profile["id"]), seconds))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.get("/poller/settings")
def poller_settings_get():
    profile, error = _profile_or_error()
    if error:
        return error
    pid = int(profile["id"])
    settings = poller_control.get_settings(pid)
    return ok({"settings": settings, "runtime": poller_control.snapshot(pid, settings)})


@bp.post("/poller/settings")
def poller_settings_save():
    profile, error = _profile_or_error()
    if error:
        return error
    try:
        return ok({"settings": poller_control.save_settings(int(profile["id"]), request.get_json(silent=True) or {})})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
