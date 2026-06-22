from __future__ import annotations
import threading
import time
import json
import psutil
from flask_socketio import emit, join_room, leave_room, disconnect
from .preferences import active_profile, get_profile
from ..db import default_user_id
from .torrent_cache import torrent_cache
from .torrent_summary import cached_summary
from . import rtorrent, smart_queue, traffic_history, automation_rules, torrent_stats, auth, speed_peaks, poller_control, download_planner, profile_speed_limits


def _profile_room(profile_id: int) -> str:
    return f"profile:{int(profile_id)}"


def _poller_profiles() -> list[dict]:
    from ..db import connect
    with connect() as conn:
        # Note: Background polling must be profile-scoped and browser-independent, even when auth is disabled.
        return conn.execute("SELECT * FROM rtorrent_profiles ORDER BY id").fetchall()


def emit_profile_event(socketio, event: str, payload: dict, profile_id: int) -> None:
    scoped_payload = {**(payload or {}), "profile_id": int(profile_id)}
    socketio.emit(event, scoped_payload, to=_profile_room(profile_id))


def _emit_profile(socketio, event: str, payload: dict, profile_id: int) -> None:
    emit_profile_event(socketio, event, payload, profile_id)


_speed_limits_applied: dict[int, tuple[int, int]] = {}


def _apply_configured_speed_limits(profile: dict, *, force: bool = False) -> None:
    profile_id = int(profile.get("id") or 0)
    limits = profile_speed_limits.get_limits(profile_id)
    if not limits.get("configured"):
        return
    key = (int(limits.get("down") or 0), int(limits.get("up") or 0))
    if not force and _speed_limits_applied.get(profile_id) == key:
        return
    # Note: Persisted per-profile limits are applied by the backend poller, not only after browser profile selection.
    rtorrent.set_limits(profile, limits.get("down"), limits.get("up"))
    _speed_limits_applied[profile_id] = key


def _run_slow_profile_tasks(socketio, profile: dict, profile_id: int) -> None:
    state = poller_control.state_for(profile_id)
    profile_user_id = int(profile.get("user_id") or default_user_id())
    try:
        try:
            torrent_stats.queue_refresh(socketio, profile, force=False, room=_profile_room(profile_id))
        except Exception as exc:
            _emit_profile(socketio, "torrent_stats_update", {"ok": False, "profile_id": profile_id, "error": str(exc)}, profile_id)
        try:
            result = smart_queue.check(profile, user_id=profile_user_id, force=False)
            if result.get("enabled"):
                _emit_profile(socketio, "smart_queue_update", result, profile_id)
                if result.get("stopped") or result.get("started") or result.get("start_requested") or result.get("paused") or result.get("resumed"):
                    queue_diff = torrent_cache.refresh(profile)
                    if queue_diff.get("ok"):
                        payload = {**queue_diff, "summary": cached_summary(profile_id, torrent_cache.snapshot(profile_id), force=True)}
                        _emit_profile(socketio, "torrent_patch", payload, profile_id)
        except Exception as exc:
            _emit_profile(socketio, "smart_queue_update", {"ok": False, "profile_id": profile_id, "error": str(exc)}, profile_id)
        try:
            auto_result = automation_rules.check(profile, user_id=profile_user_id, force=False)
            if auto_result.get("applied") or auto_result.get("batches"):
                _emit_profile(socketio, "automation_update", auto_result, profile_id)
        except Exception as exc:
            _emit_profile(socketio, "automation_update", {"ok": False, "profile_id": profile_id, "error": str(exc)}, profile_id)
        try:
            plan_result = download_planner.enforce(profile, force=False, user_id=profile_user_id)
            if plan_result.get("enabled") and not plan_result.get("skipped"):
                _emit_profile(socketio, "download_plan_update", plan_result, profile_id)
        except Exception as exc:
            _emit_profile(socketio, "download_plan_update", {"ok": False, "profile_id": profile_id, "error": str(exc)}, profile_id)
    finally:
        state.slow_task_running = False


def _is_active_rows(rows: list[dict]) -> bool:
    for row in rows or []:
        try:
            if int(row.get("state") or 0) and (int(row.get("down_rate") or 0) > 0 or int(row.get("up_rate") or 0) > 0):
                return True
        except Exception:
            continue
    return False


def _speed_status_from_rows(profile_id: int, rows: list[dict]) -> dict:
    down_rate = sum(int(row.get("down_rate") or 0) for row in rows or [])
    up_rate = sum(int(row.get("up_rate") or 0) for row in rows or [])
    return {
        "profile_id": int(profile_id),
        "down_rate": down_rate,
        "up_rate": up_rate,
        "down_rate_h": rtorrent.human_rate(down_rate),
        "up_rate_h": rtorrent.human_rate(up_rate),
        "speed_peaks": speed_peaks.record(profile_id, down_rate, up_rate),
    }


_started = False
_start_lock = threading.Lock()


def register_socketio_handlers(socketio):

    def poller():
        while True:
            loop_started = time.monotonic()
            next_sleep = 10.0
            for profile in _poller_profiles():
                if not profile:
                    continue
                pid = int(profile["id"])
                settings = poller_control.get_settings(pid)
                state = poller_control.state_for(pid)
                now = time.monotonic()
                live_interval = poller_control.effective_live_interval(settings, state)
                list_interval = poller_control.effective_list_interval(settings, state)
                next_sleep = min(
                    next_sleep,
                    max(poller_control.MIN_POLL_INTERVAL_SECONDS, live_interval - (now - state.last_live_at)),
                    max(poller_control.MIN_POLL_INTERVAL_SECONDS, list_interval - (now - state.last_list_at)),
                    max(poller_control.MIN_POLL_INTERVAL_SECONDS, float(settings["system_stats_interval_seconds"]) - (now - state.last_system_at)),
                    max(poller_control.MIN_POLL_INTERVAL_SECONDS, float(settings["slow_stats_interval_seconds"]) - (now - state.last_slow_at)),
                    max(poller_control.MIN_POLL_INTERVAL_SECONDS, float(settings["queue_stats_interval_seconds"]) - (now - state.last_queue_at)),
                )

                run_live = poller_control.should_live_poll(now, settings, state)
                run_list = poller_control.should_list_poll(now, settings, state)
                run_system = poller_control.should_system_poll(now, settings, state)
                run_slow = poller_control.should_slow_poll(now, settings, state)
                run_queue = poller_control.should_queue_poll(now, settings, state)
                if not (run_live or run_list or run_system or run_slow or run_queue):
                    continue

                tick_started = time.monotonic()
                changed = False
                ok = True
                error = ""
                active = state.last_active
                emitted_payload_size = 0
                rtorrent_call_count = 0
                skipped_emissions = 0
                heartbeat = {"ok": True, "profile_id": pid, "tick": state.tick_count + 1, "error": ""}

                try:
                    # Note: This keeps per-profile runtime limits active after app start, without waiting for UI contact.
                    _apply_configured_speed_limits(profile)
                    rows = torrent_cache.snapshot(pid)
                    speed_status = _speed_status_from_rows(pid, rows)

                    if run_live:
                        live_started = time.monotonic()
                        live = torrent_cache.refresh_live(profile)
                        rtorrent_call_count += 1
                        state.last_live_at = now
                        state.last_fast_at = now
                        ok = bool(live.get("ok"))
                        error = str(live.get("error") or "")
                        poller_control.mark_live_poll(state, live_started, ok, error, len(live.get("updated") or []), bool(live.get("requires_full_refresh")))
                        rows = torrent_cache.snapshot(pid)
                        active = _is_active_rows(rows)
                        speed_status = _speed_status_from_rows(pid, rows) if live.get("ok") else speed_status
                        if live.get("ok"):
                            if live.get("updated") or speed_status:
                                changed = changed or bool(live.get("updated"))
                                payload = {
                                    "ok": True,
                                    "profile_id": pid,
                                    "updated": live.get("updated") or [],
                                    "speed_status": speed_status,
                                    "requires_full_refresh": bool(live.get("requires_full_refresh")),
                                }
                                emitted_payload_size += len(json.dumps(payload, default=str))
                                _emit_profile(socketio, "torrent_live_patch", payload, pid)
                            else:
                                skipped_emissions += 1
                            if live.get("requires_full_refresh"):
                                state.last_list_at = 0.0
                                run_list = True
                        else:
                            _emit_profile(socketio, "rtorrent_error", live, pid)

                    if run_list:
                        list_started = time.monotonic()
                        diff = torrent_cache.refresh(profile)
                        rtorrent_call_count += 1
                        state.last_list_at = now
                        ok = bool(diff.get("ok"))
                        error = str(diff.get("error") or "")
                        poller_control.mark_list_poll(state, list_started, ok, error, len(diff.get("added") or []), len(diff.get("updated") or []), len(diff.get("removed") or []))
                        rows = torrent_cache.snapshot(pid)
                        active = _is_active_rows(rows)
                        speed_status = _speed_status_from_rows(pid, rows) if diff.get("ok") else speed_status
                        if diff.get("ok") and (diff["added"] or diff["updated"] or diff["removed"]):
                            changed = True
                            payload = {**diff, "summary": cached_summary(pid, rows, force=True), "speed_status": speed_status}
                            emitted_payload_size += len(json.dumps(payload, default=str))
                            _emit_profile(socketio, "torrent_patch", payload, pid)
                        elif not diff.get("ok"):
                            _emit_profile(socketio, "rtorrent_error", diff, pid)
                        else:
                            skipped_emissions += 1

                    if run_system:
                        state.last_system_at = now
                        rows = torrent_cache.snapshot(pid)
                        status = rtorrent.system_status(profile, rows)
                        rtorrent_call_count += 1
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
                        status["profile_id"] = pid
                        traffic_history.record(pid, status.get("down_rate", 0), status.get("up_rate", 0), status.get("total_down", 0), status.get("total_up", 0))
                        status["speed_peaks"] = (speed_status or _speed_status_from_rows(pid, rows))["speed_peaks"]
                        status["poller"] = poller_control.snapshot(pid)
                        emitted_payload_size += len(json.dumps(status, default=str))
                        _emit_profile(socketio, "system_stats", status, pid)

                    if poller_control.should_disk_poll(now, settings, state):
                        state.last_disk_at = now

                    if poller_control.should_tracker_poll(now, settings, state):
                        state.last_tracker_at = now

                    if run_slow or run_queue:
                        if run_slow:
                            state.last_slow_at = now
                        if run_queue:
                            state.last_queue_at = now
                        if state.slow_task_running:
                            skipped_emissions += 1
                        else:
                            state.slow_task_running = True
                            socketio.start_background_task(_run_slow_profile_tasks, socketio, dict(profile), pid)
                except Exception as exc:
                    ok = False
                    error = str(exc)
                    _emit_profile(socketio, "rtorrent_error", {"profile_id": pid, "error": error}, pid)

                runtime = poller_control.mark_tick(state, tick_started, active=active, ok=ok, error=error, emitted_payload_size=emitted_payload_size, rtorrent_call_count=rtorrent_call_count, skipped_emissions=skipped_emissions, settings=settings)
                heartbeat.update({"ok": ok, "error": error, "active": active, "poller": runtime})
                if poller_control.should_heartbeat(time.monotonic(), settings, state, changed):
                    state.last_heartbeat_at = time.monotonic()
                    _emit_profile(socketio, "heartbeat", heartbeat, pid)

            elapsed = time.monotonic() - loop_started
            socketio.sleep(max(poller_control.MIN_POLL_INTERVAL_SECONDS, min(10.0, next_sleep - elapsed)))

    def ensure_poller_started():
        global _started
        with _start_lock:
            if not _started:
                socketio.start_background_task(poller)
                _started = True

    ensure_poller_started()

    @socketio.on("connect")
    def handle_connect():
        ensure_poller_started()
        if auth.enabled() and not auth.ensure_request_user():
            disconnect()
            return False
        profile = active_profile()
        if profile:
            join_room(_profile_room(profile["id"]))
        emit("connected", {"ok": True, "profile": profile})
        if not profile:
            emit("profile_required", {"ok": True, "profiles": []})
            return
        try:
            _apply_configured_speed_limits(profile, force=True)
        except Exception as exc:
            emit("rtorrent_error", {"profile_id": profile["id"], "error": str(exc)})
        rows = torrent_cache.snapshot(profile["id"])
        emit("torrent_snapshot", {"profile_id": profile["id"], "torrents": rows, "summary": cached_summary(profile["id"], rows), "speed_status": _speed_status_from_rows(profile["id"], rows)})
        emit("poller_settings", {"profile_id": int(profile["id"]), "settings": poller_control.get_settings(int(profile["id"])), "runtime": poller_control.snapshot(int(profile["id"]))})
        emit("download_plan_update", {"profile_id": int(profile["id"]), "settings": download_planner.get_settings(int(profile["id"]))})

    @socketio.on("select_profile")
    def handle_select_profile(data):
        if auth.enabled() and not auth.ensure_request_user():
            disconnect()
            return
        old_profile = active_profile()
        if old_profile:
            leave_room(_profile_room(old_profile["id"]))
        profile_id = int((data or {}).get("profile_id") or 0)
        if not profile_id:
            emit("profile_required", {"ok": True, "profiles": []})
            return
        profile = get_profile(profile_id)
        if not profile:
            emit("rtorrent_error", {"error": "Profile access denied or profile does not exist"})
            return
        join_room(_profile_room(profile_id))
        try:
            _apply_configured_speed_limits(profile, force=True)
        except Exception as exc:
            emit("rtorrent_error", {"profile_id": profile_id, "error": str(exc)})
        diff = torrent_cache.refresh(profile)
        rows = torrent_cache.snapshot(profile_id)
        emit("torrent_snapshot", {"profile_id": profile_id, "torrents": rows, "summary": cached_summary(profile_id, rows, force=True), "speed_status": _speed_status_from_rows(profile_id, rows), "error": diff.get("error", "")})
        emit("poller_settings", {"profile_id": profile_id, "settings": poller_control.get_settings(profile_id), "runtime": poller_control.snapshot(profile_id)})
        emit("download_plan_update", {"profile_id": profile_id, "settings": download_planner.get_settings(profile_id)})
