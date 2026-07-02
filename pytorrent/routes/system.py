from __future__ import annotations
from ._shared import *
import posixpath
from ..services import operation_logs
from ..services.frontend_assets import static_hash

@bp.get("/system/disk")
def system_disk():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"})
    try:
        return ok({"disk": _user_disk_status(profile)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})



@bp.get("/system/status")
def system_status():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"})
    try:
        status = rtorrent.system_status(profile)
        status["disk"] = _user_disk_status(profile)
        if bool(profile.get("is_remote")):
            try:
                usage = rtorrent.remote_system_usage(profile)
                status.update(usage)
                status["usage_available"] = True
            except Exception as exc:
                status["usage_source"] = "rtorrent-remote"
                status["usage_available"] = False
                status["usage_error"] = str(exc)
        else:
            status["cpu"] = psutil.cpu_percent(interval=None)
            status["ram"] = psutil.virtual_memory().percent
            status["usage_source"] = "local"
            status["usage_available"] = True
        status["speed_peaks"] = speed_peaks.record(profile["id"], status.get("down_rate", 0), status.get("up_rate", 0))
        return ok({"status": status})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})



@bp.get("/static_hash")
def static_hash_get():
    # Note: This returns the startup-computed JS/CSS version without scanning files per request.
    value = static_hash()
    return ok({"static_hash": value, "version": value})


@bp.get("/health")
def health_check():
    # Note: Lightweight health endpoint avoids rTorrent calls, making it safe for frequent monitoring.
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return ok({"status": "ok"})
    except Exception as exc:
        return jsonify({"ok": False, "status": "error", "error": str(exc)}), 500


@bp.get("/health/nagios")
def health_check_nagios():
    # Note: Plain-text response is compatible with simple Nagios check_http probes.
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return "OK - pyTorrent API healthy\n", 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as exc:
        return f"CRITICAL - pyTorrent API unhealthy: {exc}\n", 500, {"Content-Type": "text/plain; charset=utf-8"}


@bp.get("/app/status")
def app_status():
    started = time.perf_counter()
    profile = request_profile()
    proc = psutil.Process(os.getpid())
    try:
        jobs = list_jobs(10, 0)
        jobs_total = jobs.get("total", 0)
    except Exception:
        jobs_total = 0
    include_cleanup = str(request.args.get("cleanup") or "").lower() in {"1", "true", "yes", "on"}
    status = {
        "pytorrent": {
            "ok": True,
            "pid": os.getpid(),
            "uptime_seconds": round(time.time() - proc.create_time(), 1),
            "memory_rss": proc.memory_info().rss,
            "memory_rss_h": rtorrent.human_size(proc.memory_info().rss),
            "threads": proc.num_threads(),
            "cpu_percent": proc.cpu_percent(interval=None),
            "jobs_total": jobs_total,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "executable": sys.executable,
            "worker_threads": WORKERS,
            "open_files": _safe_len(proc.open_files) if hasattr(proc, "open_files") else None,
            "connections": _safe_len(lambda: proc.net_connections(kind="inet")) if hasattr(proc, "net_connections") else None,
        },
        "profile": profile,
        "scgi": None,
    }
    if include_cleanup:
        status["cleanup"] = cleanup_summary()
    if profile:
        try:
            status["scgi"] = rtorrent.scgi_diagnostics(profile)
        except Exception as exc:
            status["scgi"] = {"ok": False, "error": str(exc), "url": profile.get("scgi_url")}
        try:
            # Note: The diagnostics panel shows the same DL/UL records as the footer.
            status["speed_peaks"] = speed_peaks.current(profile["id"])
        except Exception as exc:
            status["speed_peaks"] = {"error": str(exc)}
        try:
            # Note: App status carries poller settings and runtime so the panel still renders when the separate poller endpoint is unavailable.
            poller_settings = poller_control.get_settings(int(profile["id"]))
            status["poller"] = {"settings": poller_settings, "runtime": poller_control.snapshot(int(profile["id"]), poller_settings)}
        except Exception as exc:
            status["poller"] = {"settings": {}, "runtime": {}, "error": str(exc)}
    try:
        prefs = preferences.get_preferences()
        status["port_check"] = {"status": "disabled", "enabled": False} if not bool((prefs or {}).get("port_check_enabled")) else port_check_status(force=False)
    except Exception as exc:
        status["port_check"] = {"status": "error", "error": str(exc)}
    try:
        from ..services import background_cache_warmup
        status["background_cache_warmup"] = background_cache_warmup.status()
    except Exception as exc:
        status["background_cache_warmup"] = {"started": False, "error": str(exc)}
    status["api_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return ok({"status": status})



@bp.get("/port-check")
def port_check_get():
    prefs = preferences.get_preferences()
    if not bool((prefs or {}).get("port_check_enabled")):
        return ok({"port_check": {"status": "disabled", "enabled": False}})
    return ok({"port_check": port_check_status(force=False)})



@bp.post("/port-check")
def port_check_post():
    return ok({"port_check": port_check_status(force=True)})



@bp.get("/jobs")
def jobs_list():
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    data = list_jobs(limit, offset)
    return ok({"jobs": data["rows"], "total": data["total"], "limit": data["limit"], "offset": data["offset"]})



@bp.post("/jobs/clear")
def jobs_clear():
    if str(request.args.get("force") or "").lower() in {"1", "true", "yes"}:
        # Note: Emergency cleanup keeps the endpoint behavior unchanged, while force=1 enables rescue mode.
        deleted = emergency_clear_jobs()
        return ok({"deleted": deleted, "emergency": True})
    deleted = clear_jobs()
    return ok({"deleted": deleted, "emergency": False})



@bp.get("/cleanup/summary")
def cleanup_status():
    return ok({"cleanup": cleanup_summary()})



@bp.post("/cleanup/cache")
def cleanup_profile_cache():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    profile_id = int(profile["id"])
    deleted: dict[str, int | dict] = {}
    # Note: Profile cache cleanup removes derived cache only. Torrents, preferences, rules and history stay intact.
    deleted["torrent_cache_rows"] = torrent_cache.clear_profile(profile_id)
    try:
        from ..services.torrent_summary import invalidate_summary
        invalidate_summary(profile_id)
        deleted["torrent_summary"] = 1
    except Exception:
        deleted["torrent_summary"] = 0
    try:
        runtime = rtorrent.clear_profile_runtime_caches(profile_id)
    except Exception as exc:
        runtime = {"error": str(exc)}
    deleted["runtime"] = runtime
    with connect() as conn:
        exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='torrent_stats_cache'").fetchone()
        deleted["torrent_stats_cache"] = int((conn.execute("DELETE FROM torrent_stats_cache WHERE profile_id=?", (profile_id,)).rowcount if exists else 0) or 0)
        exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tracker_summary_cache'").fetchone()
        deleted["tracker_summary_cache"] = int((conn.execute("DELETE FROM tracker_summary_cache WHERE profile_id=?", (profile_id,)).rowcount if exists else 0) or 0)
        conn.execute("DELETE FROM app_settings WHERE key LIKE ?", (f"port_check:{profile_id}:%",))
    return ok({"deleted": deleted, "cleanup": cleanup_summary()})


@bp.post("/cleanup/jobs")
def cleanup_jobs():
    deleted = clear_jobs()
    return ok({"deleted": deleted, "cleanup": cleanup_summary()})


@bp.post("/cleanup/database/vacuum")
def cleanup_database_vacuum():
    require_admin()
    data = request.get_json(silent=True) or {}
    try:
        result = database_maintenance.vacuum_database(force=bool(data.get("force")))
        return ok({"vacuum": result, "cleanup": cleanup_summary()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "cleanup": cleanup_summary()}), 400



@bp.post("/cleanup/smart-queue")
def cleanup_smart_queue():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    profile_id = int(profile["id"])
    with connect() as conn:
        exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='smart_queue_history'").fetchone()
        if not exists:
            deleted = 0
        else:
            # Note: Cleanup is limited to the active profile so read/write permissions never affect other profiles.
            cur = conn.execute("DELETE FROM smart_queue_history WHERE profile_id=?", (profile_id,))
            deleted = int(cur.rowcount or 0)
    return ok({"deleted": deleted, "cleanup": cleanup_summary()})



@bp.post("/cleanup/operation-logs")
def cleanup_operation_logs():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    # Note: Operation log cleanup removes only profile-scoped log entries; torrents, jobs and settings stay intact.
    deleted = operation_logs.clear(int(profile["id"]))
    return ok({"deleted": deleted, "cleanup": cleanup_summary()})



@bp.post("/cleanup/planner")
def cleanup_planner():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    # Note: Planner cleanup removes only the active profile action history, not saved Planner settings.
    deleted = download_planner.clear_history(int(profile["id"]))
    return ok({"deleted": deleted, "cleanup": cleanup_summary()})


@bp.post("/cleanup/automations")
def cleanup_automations():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    profile_id = int(profile["id"])
    with connect() as conn:
        exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='automation_history'").fetchone()
        if not exists:
            deleted = 0
        else:
            # Note: Automation history is profile-scoped and can include rules owned by multiple users.
            cur = conn.execute("DELETE FROM automation_history WHERE profile_id=?", (profile_id,))
            deleted = int(cur.rowcount or 0)
    return ok({"deleted": deleted, "cleanup": cleanup_summary()})





@bp.post("/cleanup/poller-diagnostics")
def cleanup_poller_diagnostics():
    profile = request_profile()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    profile_id = int(profile["id"])
    # Note: This cleanup clears only in-memory poller diagnostics; polling, settings and torrent state are preserved.
    runtime = poller_control.reset_runtime_stats(profile_id)
    return ok({"deleted": {"poller_runtime_counters": 1}, "runtime": runtime, "cleanup": cleanup_summary()})

@bp.post("/cleanup/all")
def cleanup_all():
    deleted_jobs = clear_jobs()
    active_profile = request_profile()
    active_profile_id = int(active_profile["id"]) if active_profile else 0
    deleted_logs = operation_logs.clear(active_profile_id) if active_profile_id else 0
    deleted_planner = download_planner.clear_history(active_profile_id) if active_profile_id else 0
    with connect() as conn:
        exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='smart_queue_history'").fetchone()
        if not exists:
            deleted_smart = 0
        else:
            cur = conn.execute("DELETE FROM smart_queue_history WHERE profile_id=?", (active_profile_id,))
            deleted_smart = int(cur.rowcount or 0)
        exists_auto = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='automation_history'").fetchone()
        if not exists_auto:
            deleted_auto = 0
        else:
            # Note: Full cleanup clears automation history for the active profile, regardless of rule owner.
            cur = conn.execute("DELETE FROM automation_history WHERE profile_id=?", (active_profile_id,))
            deleted_auto = int(cur.rowcount or 0)
    return ok({"deleted": {"jobs": deleted_jobs, "smart_queue_history": deleted_smart, "operation_logs": deleted_logs, "planner_history": deleted_planner, "automation_history": deleted_auto}, "cleanup": cleanup_summary()})



@bp.post("/jobs/<job_id>/cancel")
def jobs_cancel(job_id: str):
    require_profile_write(_job_profile_id(job_id))
    if not cancel_job(job_id):
        return jsonify({"ok": False, "error": "Only unfinished jobs can be cancelled"}), 400
    return ok({"emergency": True})



@bp.post("/jobs/<job_id>/force")
def jobs_force(job_id: str):
    require_profile_write(_job_profile_id(job_id))
    if not force_job(job_id):
        return jsonify({"ok": False, "error": "Only pending jobs can be forced"}), 400
    return ok({"job_id": job_id})


@bp.post("/jobs/<job_id>/retry")
def jobs_retry(job_id: str):
    require_profile_write(_job_profile_id(job_id))
    if not retry_job(job_id):
        return jsonify({"ok": False, "error": "Only failed or cancelled jobs can be retried"}), 400
    return ok()



def _remote_path_contains(base: str, candidate: str) -> bool:
    base = posixpath.normpath(str(base or "").rstrip("/") or "/")
    candidate = posixpath.normpath(str(candidate or "").rstrip("/") or "/")
    return candidate == base or candidate.startswith(base.rstrip("/") + "/")


def _path_has_cached_torrents(profile_id: int, path: str) -> bool:
    # Note: The cache check prevents renaming folders that are currently known as torrent locations.
    if not str(path or "").strip():
        return False
    return any(_remote_path_contains(path, item.get("path") or "") for item in torrent_cache.snapshot(profile_id))


def _annotate_path_directories(profile: dict, payload: dict) -> dict:
    dirs = payload.get("dirs") or []
    for item in dirs:
        item_path = item.get("path") or ""
        has_torrents = _path_has_cached_torrents(int(profile.get("id") or 0), item_path)
        is_empty = bool(item.get("empty"))
        item["has_torrents"] = has_torrents
        item["can_rename"] = is_empty and not has_torrents
        # Note: The picker exposes a short reason so disabled rename buttons explain the safety rule.
        item["rename_reason"] = "Rename folder" if item["can_rename"] else ("Folder contains a known torrent path" if has_torrents else "Only empty folders can be renamed")
    return payload


def _path_profile_from_request(*, require_write_access: bool = False):
    profile_id = 0
    try:
        profile_id = int((request.args.get("profile_id") if request.method == "GET" else (request.get_json(silent=True) or {}).get("profile_id")) or 0)
    except Exception:
        profile_id = 0
    profile = preferences.get_profile(profile_id, auth.current_user_id() or default_user_id()) if profile_id else request_profile()
    if profile and require_write_access:
        require_profile_write(profile.get("id"))
    return profile


@bp.get("/path/default")
def path_default():
    profile = _path_profile_from_request()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    try:
        return ok({"path": active_default_download_path(profile), "profile_default_path": rtorrent.default_download_path(profile)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400



@bp.get("/path/browse")
def path_browse():
    profile = _path_profile_from_request()
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    base = request.args.get("path") or ""
    try:
        return ok(_annotate_path_directories(profile, rtorrent.browse_path(profile, base)))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/path/directories")
def path_directory_create():
    profile = _path_profile_from_request(require_write_access=True)
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    data = request.get_json(silent=True) or {}
    try:
        # Note: This endpoint only creates an empty directory and does not alter any torrent state.
        result = rtorrent.create_directory(profile, data.get("parent") or "", data.get("name") or "")
        return ok({"directory": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.post("/path/directories/rename")
def path_directory_rename():
    profile = _path_profile_from_request(require_write_access=True)
    if not profile:
        return jsonify({"ok": False, "error": "No profile"}), 400
    data = request.get_json(silent=True) or {}
    path = str(data.get("path") or "").strip()
    if _path_has_cached_torrents(int(profile.get("id") or 0), path):
        return jsonify({"ok": False, "error": "Directory contains a known torrent path"}), 400
    try:
        # Note: The service also verifies that the remote directory is empty before renaming.
        result = rtorrent.rename_empty_directory(profile, path, data.get("new_name") or "")
        return ok({"directory": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400



@bp.get('/rtorrent-config')
def rtorrent_config_get():
    profile = request_profile()
    if not profile:
        return jsonify({'ok': False, 'error': 'No profile'}), 400
    try:
        return ok({'config': rtorrent.get_config(profile)})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@bp.post('/rtorrent-config')
def rtorrent_config_save():
    profile = request_profile()
    if not profile:
        return jsonify({'ok': False, 'error': 'No profile'}), 400
    try:
        data = request.get_json(silent=True) or {}
        result = rtorrent.set_config(profile, data.get('values') or {}, bool(data.get('apply_now', True)), bool(data.get('apply_on_start')), data.get('clear_keys') or [])
        if not result.get('ok'):
            return jsonify({'ok': False, 'error': 'Some settings were not saved', 'result': result}), 400
        return ok({'result': result})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500




@bp.post('/rtorrent-config/reset')
def rtorrent_config_reset():
    profile = request_profile()
    if not profile:
        return jsonify({'ok': False, 'error': 'No profile'}), 400
    try:
        # Note: This clears only pyTorrent-saved interface overrides and then reloads live rTorrent values.
        return ok({'config': rtorrent.reset_config_overrides(profile)})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400

@bp.post('/rtorrent-config/generate')
def rtorrent_config_generate():
    profile = request_profile()
    if not profile:
        return jsonify({'ok': False, 'error': 'No profile'}), 400
    try:
        data = request.get_json(silent=True) or {}
        return ok({'config_text': rtorrent.generate_config_text(data.get('values') or {})})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@bp.get('/traffic/history')
def traffic_history_get():
    from ..services import traffic_history
    profile = request_profile()
    if not profile:
        return ok({'history': {'range': request.args.get('range') or '7d', 'bucket': 'day', 'rows': []}})
    range_name = request.args.get('range') or '7d'
    if range_name not in {'15m', '1h', '3h', '6h', '24h', '7d', '30d', '90d'}:
        range_name = '7d'
    try:
        try:
            from ..services import rtorrent
            status = rtorrent.system_status(profile)
            traffic_history.record(profile['id'], status.get('down_rate', 0), status.get('up_rate', 0), status.get('total_down', 0), status.get('total_up', 0), force=True)
        except Exception:
            pass
        return ok({'history': traffic_history.history(profile['id'], range_name)})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc), 'history': {'range': range_name, 'rows': []}})

