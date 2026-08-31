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


def _profile_or_error(require_write: bool = False):
    """Resolve planner profile context with explicit write enforcement for mutations."""
    # Note: Service-level checks remain defense in depth; route selection returns 403 before any shared state changes.
    profile = request_profile(require_write=require_write)
    if not profile:
        return None, (jsonify({"ok": False, "error": "No profile"}), 400)
    return profile, None


@bp.get("/download-planner")
def download_planner_get():
    profile, error = _profile_or_error()
    if error:
        return error
    profile_id = int(profile["id"])
    # Note: Profile id is echoed so slow planner loads cannot repaint another active profile.
    return ok({"settings": download_planner.get_settings(profile_id, current_user_id()), "profile_id": profile_id})


@bp.post("/download-planner")
def download_planner_save():
    # Note: Planner settings are saved through one canonical endpoint to keep the frontend/backend contract explicit.
    profile, error = _profile_or_error(require_write=True)
    if error:
        return error
    try:
        profile_id = int(profile["id"])
        settings = download_planner.save_settings(profile_id, request.get_json(silent=True) or {}, current_user_id())
        return ok({"settings": settings, "profile_id": profile_id})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/download-planner/check")
def download_planner_check():
    profile, error = _profile_or_error(require_write=True)
    if error:
        return error
    try:
        data = request.get_json(silent=True) or {}
        run_profile = dict(profile)
        if data.get("dry_run"):
            run_profile["dry_run"] = "true"
        return ok({"result": download_planner.enforce(run_profile, force=True, user_id=current_user_id())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.get("/download-planner/preview")
def download_planner_preview():
    # Note: Preview/history echo their source profile so the browser can reject delayed cross-profile results.
    profile, error = _profile_or_error()
    if error:
        return error
    profile_id = int(profile["id"])
    return ok({"preview": download_planner.preview(profile, user_id=current_user_id()), "history": download_planner.history(profile_id, int(request.args.get("history_limit") or 40)), "history_total": download_planner.history_count(profile_id), "profile_id": profile_id})


@bp.delete("/download-planner/history")
def download_planner_history_clear():
    profile, error = _profile_or_error(require_write=True)
    if error:
        return error
    try:
        deleted = download_planner.clear_history(int(profile["id"]))
        return ok({"deleted": deleted, "history": [], "history_total": 0})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/download-planner/override")
def download_planner_override():
    profile, error = _profile_or_error(require_write=True)
    if error:
        return error
    try:
        seconds = int((request.get_json(silent=True) or {}).get("seconds") or 0)
        return ok(download_planner.set_manual_override(int(profile["id"]), seconds))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.get("/poller/settings")
def poller_settings_get():
    # Note: Poller reads echo the resolved profile id for frontend race protection.
    profile, error = _profile_or_error()
    if error:
        return error
    pid = int(profile["id"])
    settings = poller_control.get_settings(pid)
    return ok({"settings": settings, "runtime": poller_control.snapshot(pid, settings), "profile_id": pid})


@bp.post("/poller/settings")
def poller_settings_save():
    # Note: Poller saves echo the resolved profile id so stale save responses cannot repaint another profile.
    profile, error = _profile_or_error(require_write=True)
    if error:
        return error
    try:
        profile_id = int(profile["id"])
        return ok({"settings": poller_control.save_settings(profile_id, request.get_json(silent=True) or {}), "profile_id": profile_id})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
