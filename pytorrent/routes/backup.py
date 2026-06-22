from __future__ import annotations
from ._shared import *
from ..services import auth


def _active_profile_id(require_write: bool = False) -> int | None:
    profile = request_profile(require_write=require_write)
    return int(profile["id"]) if profile else None


@bp.get("/backup")
def backup_list():
    uid = default_user_id()
    pid = _active_profile_id()
    can_app = auth.is_admin()
    return ok({
        "profile_backups": backup_service.list_backups(uid, "profile", pid) if pid else [],
        "app_backups": backup_service.list_backups(uid, "app") if can_app else [],
        "profile_auto": backup_service.get_auto_backup_settings(uid, "profile", pid) if pid else None,
        "app_auto": backup_service.get_auto_backup_settings(uid, "app") if can_app else None,
        "auto": backup_service.get_auto_backup_settings(uid, "app") if can_app else None,
        "can_app_backup": can_app,
    })


@bp.post("/backup/profile")
def backup_create_profile():
    data = request.get_json(silent=True) or {}
    pid = _active_profile_id(require_write=True)
    if not pid:
        return jsonify({"ok": False, "error": "No profile"}), 400
    try:
        return ok({
            "backup": backup_service.create_profile_backup(str(data.get("name") or "Profile backup"), pid, default_user_id()),
            "profile_backups": backup_service.list_backups(default_user_id(), "profile", pid),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/backup/app")
def backup_create_app():
    data = request.get_json(silent=True) or {}
    try:
        return ok({
            "backup": backup_service.create_app_backup(str(data.get("name") or "Application backup"), default_user_id()),
            "app_backups": backup_service.list_backups(default_user_id(), "app"),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403 if isinstance(exc, PermissionError) else 400


@bp.post("/backup")
def backup_create():
    return backup_create_profile()


@bp.get("/backup/settings")
def backup_settings_get():
    if not auth.is_admin():
        return jsonify({"ok": False, "error": "Application backup settings are admin-only"}), 403
    return ok({"settings": backup_service.get_auto_backup_settings(default_user_id(), "app")})


@bp.post("/backup/settings")
def backup_settings_save():
    data = request.get_json(silent=True) or {}
    try:
        return ok({"settings": backup_service.save_auto_backup_settings(data, default_user_id(), "app")})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403 if isinstance(exc, PermissionError) else 400


@bp.get("/backup/profile/settings")
def profile_backup_settings_get():
    pid = _active_profile_id()
    if not pid:
        return jsonify({"ok": False, "error": "No profile"}), 400
    return ok({"settings": backup_service.get_auto_backup_settings(default_user_id(), "profile", pid)})


@bp.post("/backup/profile/settings")
def profile_backup_settings_save():
    data = request.get_json(silent=True) or {}
    pid = _active_profile_id(require_write=True)
    if not pid:
        return jsonify({"ok": False, "error": "No profile"}), 400
    try:
        return ok({"settings": backup_service.save_auto_backup_settings(data, default_user_id(), "profile", pid)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403 if isinstance(exc, PermissionError) else 400


@bp.get("/backup/<int:backup_id>/preview")
def backup_preview(backup_id: int):
    try:
        return ok({"preview": backup_service.preview_backup(backup_id, default_user_id())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/backup/<int:backup_id>/restore")
def backup_restore(backup_id: int):
    try:
        pid = _active_profile_id(require_write=True)
        return ok({"result": backup_service.restore_backup(backup_id, default_user_id(), profile_id=pid)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403 if isinstance(exc, PermissionError) else 400


@bp.delete("/backup/<int:backup_id>")
def backup_delete(backup_id: int):
    try:
        return ok({"result": backup_service.delete_backup(backup_id, default_user_id())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.get("/backup/<int:backup_id>/download")
def backup_download(backup_id: int):
    try:
        payload = backup_service.payload_for_backup(backup_id, default_user_id())
        tmp = tempfile.NamedTemporaryFile(prefix="pytorrent-backup-", suffix=".json", delete=False, mode="w", encoding="utf-8")
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.close()
        return send_file(tmp.name, as_attachment=True, download_name=f"pytorrent-{payload.get('backup_type') or 'backup'}-{backup_id}.json")
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
