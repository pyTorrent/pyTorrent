from __future__ import annotations
from ._shared import *
from ..services.rtorrent.diagnostics import profile_diagnostics
from ..services import auth
from ..utils import human_size

@bp.get("/profiles")
def profiles_list():
    profiles = []
    for row in preferences.list_profiles():
        item = dict(row)
        # Note: Frontend actions can hide write-only operations without trusting this flag; backend still enforces permissions.
        item["can_write"] = auth.can_write_profile(int(item.get("id") or 0), auth.current_user_id() or default_user_id())
        stats = preferences.get_profile_runtime_stats(int(item.get("id") or 0))
        if stats:
            stats["total_size_h"] = human_size(stats.get("total_size_bytes"))
            stats["completed_h"] = human_size(stats.get("completed_bytes"))
            stats["downloaded_h"] = human_size(stats.get("downloaded_bytes"))
            stats["uploaded_h"] = human_size(stats.get("uploaded_bytes"))
            item["runtime_stats"] = stats
        settings = backup_service.get_auto_backup_settings(default_user_id(), "profile", int(item.get("id") or 0))
        item["profile_backup_enabled"] = bool(settings.get("enabled"))
        item["profile_backup_interval_hours"] = settings.get("interval_hours")
        item["profile_backup_retention_days"] = settings.get("retention_days")
        profiles.append(item)
    return ok({"profiles": profiles, "active": preferences.active_profile()})



@bp.post("/profiles")
def profiles_create():
    try:
        return ok({"profile": preferences.save_profile(request.json or {})})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.put("/profiles/<int:profile_id>")
def profiles_update(profile_id: int):
    try:
        return ok({"profile": preferences.update_profile(profile_id, request.json or {})})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400



@bp.delete("/profiles/<int:profile_id>")
def profiles_delete(profile_id: int):
    preferences.delete_profile(profile_id)
    return ok({"profiles": preferences.list_profiles(), "active": preferences.active_profile()})



@bp.post("/profiles/<int:profile_id>/activate")
def profiles_activate(profile_id: int):
    try:
        profile = preferences.activate_profile(profile_id)
        stats_error = ""
        try:
            # Note: Profile overview metrics are cached only on user-initiated profile switch, not on every profile list render.
            preferences.save_profile_runtime_stats(profile, rtorrent.list_torrents(profile), user_id=auth.current_user_id() or default_user_id())
        except Exception as exc:
            stats_error = str(exc)
        response = {"profile": profile}
        if stats_error:
            response["stats_error"] = stats_error
        return ok(response)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404



@bp.post("/profiles/test")
def profiles_test_unsaved():
    data = request.get_json(silent=True) or {}
    profile = {
        "id": data.get("id"),
        "name": data.get("name") or "test",
        "scgi_url": data.get("scgi_url") or "",
        "timeout_seconds": data.get("timeout_seconds") or 5,
    }
    return ok({"diagnostics": profile_diagnostics(profile)})


@bp.get("/profiles/<int:profile_id>/diagnostics")
def profiles_diagnostics(profile_id: int):
    profile = preferences.get_profile(profile_id)
    if not profile:
        return jsonify({"ok": False, "error": "Profile not found"}), 404
    return ok({"diagnostics": profile_diagnostics(profile)})


@bp.get("/profiles/diagnostics")
def profiles_diagnostics_all():
    rows = preferences.list_profiles()
    diagnostics = []
    for profile in rows:
        diagnostics.append(profile_diagnostics(profile))
    return ok({"diagnostics": diagnostics})


@bp.get("/profiles/export")
def profiles_export():
    return ok(preferences.export_profiles())


@bp.post("/profiles/import")
def profiles_import():
    try:
        rows = preferences.import_profiles(request.get_json(silent=True) or {})
        return ok({"profiles": rows})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.get("/preferences")
def prefs_get():
    return ok({"preferences": preferences.get_preferences(profile_id=request_profile_id())})



@bp.post("/preferences")
def prefs_save():
    return ok({"preferences": preferences.save_preferences(request.json or {}, profile_id=request_profile_id(require_write=True))})


@bp.post("/preferences/table-columns/recommended")
def prefs_table_columns_recommended():
    # Note: Applies the backend-owned recommended desktop and mobile column layout.
    return ok({"preferences": preferences.apply_recommended_table_columns(profile_id=request_profile_id(require_write=True))})



@bp.get("/labels")
def labels_list():
    profile = request_profile()
    pid = profile["id"] if profile else None
    if not pid:
        return ok({"labels": []})
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT l.*, COALESCE(u.display_name,u.username,u.email,'user ' || l.user_id) AS owner_name
            FROM labels l
            LEFT JOIN users u ON u.id=l.user_id
            WHERE l.profile_id=?
            ORDER BY l.name COLLATE NOCASE, l.id
            """,
            (pid,),
        ).fetchall()
    return ok({"labels": rows})



@bp.post("/labels")
def labels_save():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Missing label name"}), 400
    if not auth.can_write_profile(int(profile["id"]), default_user_id()):
        return jsonify({"ok": False, "error": "No write access to profile"}), 403
    now = utcnow()
    with connect() as conn:
        existing = conn.execute("SELECT id FROM labels WHERE profile_id=? AND lower(name)=lower(?) ORDER BY id LIMIT 1", (profile["id"], name)).fetchone()
        if existing:
            conn.execute("UPDATE labels SET color=?, updated_at=? WHERE id=?", (data.get("color") or "#64748b", now, existing["id"]))
        else:
            conn.execute("INSERT INTO labels(user_id,profile_id,name,color,created_at,updated_at) VALUES(?,?,?,?,?,?)", (default_user_id(), profile["id"], name, data.get("color") or "#64748b", now, now))
    return labels_list()



@bp.delete("/labels/<int:label_id>")
def labels_delete(label_id: int):
    profile = request_profile()
    pid = profile["id"] if profile else None
    if not pid or not auth.can_write_profile(int(pid), default_user_id()):
        return jsonify({"ok": False, "error": "No write access to profile"}), 403
    with connect() as conn:
        conn.execute("DELETE FROM labels WHERE id=? AND profile_id=?", (label_id, pid))
    return labels_list()



@bp.get("/ratio-groups")
def ratio_groups_list():
    profile = request_profile()
    pid = profile["id"] if profile else None
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT g.*, COALESCE(u.display_name,u.username,u.email,'user ' || g.user_id) AS owner_name
            FROM ratio_groups g
            LEFT JOIN users u ON u.id=g.user_id
            WHERE g.profile_id=?
            ORDER BY g.name COLLATE NOCASE, g.id
            """,
            (pid or 0,),
        ).fetchall() if pid else []
        history = conn.execute("SELECT * FROM ratio_history WHERE profile_id=? ORDER BY id DESC LIMIT 50", (pid or 0,)).fetchall() if pid else []
    return ok({"groups": rows, "history": history})



@bp.post("/ratio-groups")
def ratio_groups_save():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Missing group name"}), 400
    if not auth.can_write_profile(int(profile["id"]), default_user_id()):
        return jsonify({"ok": False, "error": "No write access to profile"}), 403
    now = utcnow()
    with connect() as conn:
        existing = conn.execute("SELECT id,user_id FROM ratio_groups WHERE profile_id=? AND lower(name)=lower(?) ORDER BY id LIMIT 1", (profile["id"], name)).fetchone()
        values = (float(data.get("min_ratio") or 1), float(data.get("max_ratio") or 2), int(data.get("seed_time_minutes") or 0), int(data.get("min_seed_time_minutes") or 0), 1 if data.get("ignore_private", True) else 0, 1 if data.get("ignore_active_upload", True) else 0, int(data.get("active_upload_min_bytes") or 1024), data.get("move_path") or "", data.get("set_label") or "", data.get("action") or "stop", 1 if data.get("enabled", True) else 0, now)
        if existing:
            conn.execute(
                """UPDATE ratio_groups SET min_ratio=?,max_ratio=?,seed_time_minutes=?,min_seed_time_minutes=?,ignore_private=?,ignore_active_upload=?,active_upload_min_bytes=?,move_path=?,set_label=?,action=?,enabled=?,updated_at=? WHERE id=? AND profile_id=?""",
                (*values, existing["id"], profile["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO ratio_groups(user_id,profile_id,name,min_ratio,max_ratio,seed_time_minutes,min_seed_time_minutes,ignore_private,ignore_active_upload,active_upload_min_bytes,move_path,set_label,action,enabled,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (default_user_id(), profile["id"], name, *values[:-1], now, now),
            )
    return ratio_groups_list()



@bp.delete("/ratio-groups/<int:group_id>")
def ratio_groups_delete(group_id: int):
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    if not auth.can_write_profile(int(profile["id"]), default_user_id()):
        return jsonify({"ok": False, "error": "No write access to profile"}), 403
    with connect() as conn:
        # Note: Deleting a ratio group removes only the group definition and its assignment links; history stays as an audit trail.
        deleted = conn.execute("DELETE FROM ratio_groups WHERE id=? AND profile_id=?", (int(group_id), int(profile["id"]))).rowcount
        conn.execute("DELETE FROM ratio_assignments WHERE group_id=? AND profile_id=?", (int(group_id), int(profile["id"])))
    if not deleted:
        return jsonify({"ok": False, "error": "Ratio group not found"}), 404
    return ratio_groups_list()


@bp.post("/ratio-groups/check")
def ratio_groups_check():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    return ok({"result": ratio_rules.check(profile, default_user_id())})


